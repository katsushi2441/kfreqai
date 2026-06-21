import asyncio
import logging
from copy import deepcopy

from fastapi import APIRouter, BackgroundTasks, Depends
from fastapi.exceptions import HTTPException

from freqtrade.constants import Config
from freqtrade.enums import RunMode
from freqtrade.exceptions import ConfigurationError, DependencyException, OperationalException
from freqtrade.misc import deep_merge_dicts
from freqtrade.rpc.api_server.api_schemas import (
    BgJobStarted,
    RecursiveAnalysisRequest,
    RecursiveAnalysisResponse,
)
from freqtrade.rpc.api_server.deps import get_config, verify_strategy
from freqtrade.rpc.api_server.webserver_bgwork import ApiBG
from freqtrade.util.progress_tracker import get_progress_tracker


logger = logging.getLogger(__name__)

# Private API, protected by authentication and webserver_mode dependency
router = APIRouter()


def __run_recursive_analysis_bg(config_loc: Config, job_id: str):
    from freqtrade.optimize.analysis.recursive_helpers import RecursiveAnalysisSubFunctions
    from freqtrade.resolvers.strategy_resolver import StrategyResolver

    job = ApiBG.jobs[job_id]
    job["is_running"] = True
    asyncio.set_event_loop(asyncio.new_event_loop())
    try:

        def ft_callback(task) -> None:
            job["progress_tasks"][str(task.id)] = {
                "progress": task.completed,
                "total": task.total,
                "description": task.description,
            }

        pt = get_progress_tracker(ft_callback=ft_callback)
        config_loc = RecursiveAnalysisSubFunctions.calculate_config_overrides(config_loc)
        strategy_obj = next(
            (
                s
                for s in StrategyResolver.search_all_objects(
                    config_loc,
                    enum_failed=False,
                    recursive=config_loc.get("recursive_strategy_search", False),
                )
                if s["name"] == config_loc["strategy"]
            ),
            None,
        )
        if not strategy_obj:
            raise ConfigurationError(f"Strategy {config_loc['strategy']} not found.")

        instance = RecursiveAnalysisSubFunctions.initialize_single_recursive_analysis(
            config_loc, strategy_obj, pt
        )

        job["result"] = {
            "strategy": instance.strategy_obj["name"],
            "startup_candles": instance._startup_candle,
            "strategy_scc": instance._strat_scc,
            "results": {
                indicator: {str(candle): diff for candle, diff in values.items()}
                for indicator, values in instance.dict_recursive.items()
            },
        }
        job["status"] = "success"
    except ConfigurationError as e:
        logger.error(f"Recursive analysis encountered a configuration Error: {e}")
        job["status"] = "failed"
        job["error"] = str(e)
    except (Exception, OperationalException, DependencyException) as e:
        logger.exception(f"Recursive analysis caused an error: {e}")
        job["status"] = "failed"
        job["error"] = str(e)
    finally:
        job["is_running"] = False
        ApiBG.analysis_running = False


@router.post("/recursive_analysis", response_model=BgJobStarted)
def api_start_recursive_analysis(
    payload: RecursiveAnalysisRequest,
    background_tasks: BackgroundTasks,
    config=Depends(get_config),
):
    if ApiBG.analysis_running:
        raise HTTPException(status_code=400, detail="Analysis is already running.")

    verify_strategy(payload.strategy)

    config_loc = deepcopy(config)
    config_loc["runmode"] = RunMode.UTIL_NO_EXCHANGE
    settings = dict(payload)
    config_loc = deep_merge_dicts(settings, config_loc, allow_null_overrides=False)

    job_id = ApiBG.get_job_id()
    ApiBG.jobs[job_id] = {
        "category": "recursive_analysis",
        "status": "pending",
        "progress": None,
        "progress_tasks": {},
        "is_running": False,
        "result": {},
        "error": None,
    }
    background_tasks.add_task(__run_recursive_analysis_bg, config_loc, job_id)
    ApiBG.analysis_running = True

    return {
        "status": "Recursive analysis started in background.",
        "job_id": job_id,
    }


@router.get("/recursive_analysis/{jobid}", response_model=RecursiveAnalysisResponse)
def api_get_recursive_analysis(jobid: str):
    if not (job := ApiBG.jobs.get(jobid)):
        raise HTTPException(status_code=404, detail="Job not found.")
    if job["category"] != "recursive_analysis":
        raise HTTPException(status_code=400, detail="Wrong job category.")

    if job["is_running"] or job["status"] == "pending":
        return {"status": "running", "running": True, "status_msg": "Analysis running"}
    if job["status"] == "failed":
        return {
            "status": "error",
            "running": False,
            "status_msg": f"Analysis failed with {job['error']}",
        }
    return {
        "status": "ended",
        "running": False,
        "status_msg": "Analysis ended",
        "result": job["result"],
    }
