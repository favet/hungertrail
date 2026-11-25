def main() -> None:
    """`uv run hungertrail` — serve the site on localhost for Cloudflare Tunnel to pick up."""
    import uvicorn

    from .config import settings

    uvicorn.run(
        "hungertrail.app:app",
        host=settings.host,
        port=settings.port,
        proxy_headers=True,
        forwarded_allow_ips="127.0.0.1",
    )
