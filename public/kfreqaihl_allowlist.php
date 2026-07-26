<?php
// kfreqaihl 招待制 allowlist。
// ここに「利用を許可するXユーザー名（@なし・小文字）」を並べる。ここに無い人は
// ログインできても利用できない（招待制）。管理者(xb_bittensor)はこの一覧に無くても常に許可。
// アンバサダーを追加/削除したら、このファイルを deploy_dashboard.sh でデプロイするだけ。
return array(
    'xb_bittensor',
    // 例: アンバサダーのXユーザー名を追記していく（@は不要・小文字で）
    // 'ambassador_taro',
    // 'ambassador_hanako',
);
