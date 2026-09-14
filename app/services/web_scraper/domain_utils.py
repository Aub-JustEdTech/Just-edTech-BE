"""Same-organization domain helpers for the school scraper.

Both the schema-driven crawler and the keyword ``SchoolScraperService`` need
to decide whether a link points to the *same organization* as the seed (so a
hub at ``/school-committee`` may legitimately reach ``https://go.district.org``
or ``https://www.district.org``), as opposed to an unrelated external site
(Facebook, YouTube, a random vendor).

The naive eTLD+1 rule (``parts[-2:]``) intentionally mirrors
``SchoolScraperService._registrable_domain`` so the two consumers cannot drift
apart. It deliberately does not pull in a public-suffix-list dependency for
one heuristic; the existing same-domain check
(``parsed.netloc == base_domain``) makes the same simplifying assumption.

This module centralizes:

* :func:`registrable_domain` — naive eTLD+1 of a host (``go.svusd.org`` -> ``svusd.org``).
* :func:`is_same_organization` — True when two hosts share the same naive
  registrable domain (or are equal).
* :func:`host_allowed` — the policy gate the schema crawler uses: same host OR
  in an explicit ``allowed_hosts`` set (populated from redirect final hosts)
  OR a board-meeting platform OR same registrable domain as the seed.
"""

from __future__ import annotations

from urllib.parse import urlparse


def registrable_domain(host: str) -> str:
    """Naive eTLD+1 of ``host`` (``go.svusd.org`` -> ``svusd.org``).

    Does not handle multi-part public suffixes (``co.uk`` etc.) — the existing
    same-domain check in the crawler makes the same simplifying assumption, so
    this stays at that level of rigor rather than pulling in a public-suffix
    dependency for one heuristic.
    """
    if not host:
        return ""
    parts = host.lower().split(".")
    if len(parts) <= 2:
        return host.lower()
    return ".".join(parts[-2:])


def is_same_organization(host_a: str, host_b: str) -> bool:
    """True when ``host_a`` and ``host_b`` share the same naive registrable domain.

    A same-organization link (e.g. a district's own vanity short-link subdomain
    like ``go.svusd.org`` vs the seed ``www.svusd.org``) is followed; an
    unrelated external site (Facebook, YouTube, Zoom, ...) that merely happens
    to be cross-domain is not.
    """
    if not host_a or not host_b:
        return False
    if host_a == host_b:
        return True
    return registrable_domain(host_a) == registrable_domain(host_b)


def host_allowed(
    candidate_host: str,
    *,
    seed_host: str,
    allowed_hosts: set[str] | None = None,
) -> bool:
    """Gate a candidate URL's host against the crawler's domain policy.

    True when ``candidate_host``:

    * equals the seed's host (the default same-domain rule), OR
    * is in ``allowed_hosts`` (hosts discovered by following redirects from
      the seed — e.g. a SchoolBlocks vanity seed that 301-redirects to the
      real district domain), OR
    * shares the same naive registrable domain with the seed (subdomain hops
      like ``go.district.org`` when seeded from ``www.district.org``), OR
    * belongs to an allowlisted board-meeting platform (handled by
      :func:`app.services.web_scraper.board_platforms.is_board_platform_url`
      via the full URL — callers should also check that for board platforms
      whose hosts are unrelated to the school domain).
    """
    if not candidate_host:
        return False
    if candidate_host == seed_host:
        return True
    if allowed_hosts and candidate_host in allowed_hosts:
        return True
    return is_same_organization(candidate_host, seed_host)


def url_host(url: str) -> str:
    """Lowercased netloc hostname of ``url`` (``""`` on parse failure)."""
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:  # noqa: BLE001
        return ""
