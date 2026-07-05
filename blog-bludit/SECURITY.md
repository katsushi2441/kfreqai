# Security Policy

  ## Reporting a Vulnerability

  If you believe you have found a security vulnerability in Bludit, please **do
  not** open a public GitHub issue, post in discussions, or disclose it on social
  media before it has been addressed.

  Instead, report it privately through GitHub's Private Vulnerability Reporting:

  **https://github.com/bludit/bludit/security/advisories/new**

  This opens a private advisory visible only to the Bludit maintainers.

  When reporting, please include:

  - A clear description of the vulnerability and its impact.
  - Steps to reproduce, including a proof of concept if possible.
  - The Bludit version, PHP version, and webserver where you reproduced it.
  - Any suggested mitigation or patch.

  ## What to Expect

  - We aim to acknowledge new reports within **5 business days**.
  - We will keep you informed as we investigate and work on a fix.
  - Once a fix is released, we will publish a GitHub Security Advisory crediting
    the reporter (unless you prefer to remain anonymous) and, where appropriate,
    request a CVE.

  ## Supported Versions

  Security fixes are provided for the **latest stable release** of Bludit on the
  `master` branch. Older versions are not supported — please upgrade before
  reporting issues against them.

  ## Scope

  In scope:

  - The Bludit core (`bl-kernel/`, `bl-plugins/` shipped with core, `bl-themes/`
    shipped with core).
  - The official admin interface.
  - Issues that cross a security boundary Bludit enforces — for example:
    unauthenticated access to protected functionality, privilege escalation
    between roles (e.g. author → editor or admin), cross-account access (IDOR),
    authentication or session bypass, path traversal, and active content (such
    as scripted SVG) smuggled through media uploads.

  Out of scope:

  - Third-party plugins and themes not maintained in this repository.
  - Issues that require an already-compromised admin account or server.
  - Findings that require administrator privileges and do not cross a privilege
    boundary. A Bludit administrator can already execute arbitrary code on the
    server by design — installing or editing plugins and themes is unrestricted
    PHP — so "an authenticated admin can achieve RCE, write files, or read the
    database" is not in itself a vulnerability. Privilege escalation from a lower
    role (author/editor) to admin, or from unauthenticated to authenticated,
    remains in scope.
  - Cross-site scripting or privilege escalation that relies on HTML or
    JavaScript embedded in **page content** by a user who already holds
    content-editing permissions (author, editor, or administrator). Bludit
    intentionally renders raw HTML and Markdown in page bodies; restricting
    content-editing access to trusted users is a deployment responsibility.
    (This applies to page content only — active content smuggled through media
    uploads, e.g. scripted SVG, is treated as a bug and fixed.)
  - Denial of service, rate-limiting, and brute-force concerns — these should be
    handled at the infrastructure layer (reverse proxy / WAF / Cloudflare).
  - Cryptographic algorithm choices (e.g. the password hashing function),
    identifier predictability, and similar defense-in-depth findings that are not
    accompanied by a working exploit demonstrating concrete impact on a supported
    deployment. We may adopt these as hardening, but they are not treated as
    advisories.
  - Race conditions, file-locking, or data-integrity edge cases without a
    demonstrated, attacker-controllable exploit.
  - Self-XSS, missing security headers without a demonstrated impact, and other
    best-practice findings without a concrete exploit.

  Severity and scope are assessed independently by the maintainers; a CVSS score
  submitted with a report does not by itself establish impact.

  Thank you for helping keep Bludit and its users safe.
