"""Bounded, read-only search in a local Chromium browser."""

from __future__ import annotations

import base64
import re
from urllib.parse import parse_qs, quote_plus, urlparse

from playwright.sync_api import sync_playwright


SEARCH_ENGINES = (
    ("Bing", "https://www.bing.com/search?q={query}&setlang=en-US&cc=US", "li.b_algo h2 a"),
    ("Google", "https://www.google.com/search?q={query}", "#search a:has(h3)"),
)


def result_url(href: str) -> str:
    """Bing wraps organic links in a click URL; recover the actual destination."""
    parsed = urlparse(href)
    if parsed.hostname in {"bing.com", "www.bing.com"} and parsed.path.startswith("/ck/"):
        encoded = parse_qs(parsed.query).get("u", [""])[0]
        if encoded.startswith("a1"):
            try:
                return base64.urlsafe_b64decode(encoded[2:] + "===").decode("utf-8")
            except (ValueError, UnicodeDecodeError):
                return ""
    return href


def browser_search_query(query: str, limit: int = 8) -> list[dict[str, str]]:
    """Search public results; never log in, submit forms, or bypass a CAPTCHA."""
    query = " ".join(query.split())[:240]
    if not query:
        return []
    site_match = re.search(r"\bsite:([a-z0-9.-]+)", query, re.I)
    required_host = site_match.group(1).lower() if site_match else None
    errors: list[str] = []
    results: list[dict[str, str]] = []
    seen: set[str] = set()
    with sync_playwright() as playwright:
        browser = None
        for channel in ("msedge", "chrome", None):
            try:
                browser = playwright.chromium.launch(channel=channel, headless=True)
                break
            except Exception as exc:
                errors.append(f"{channel or 'Chromium'}: {type(exc).__name__}")
        if browser is None:
            raise RuntimeError("无法启动本机 Edge/Chrome 浏览器；请安装浏览器或 Playwright Chromium。" + "; ".join(errors))
        try:
            page = browser.new_page(locale="en-US")
            for name, template, selector in SEARCH_ENGINES:
                try:
                    page.goto(template.format(query=quote_plus(query)), wait_until="domcontentloaded", timeout=25000)
                    page.locator(selector).first.wait_for(timeout=6000)
                    matches = page.locator(selector)
                    for index in range(min(matches.count(), limit * 3)):
                        item = matches.nth(index)
                        url = result_url(item.get_attribute("href") or "")
                        title = (item.text_content(timeout=3000) or "").strip()
                        host = (urlparse(url).hostname or "").lower()
                        if not url.startswith("https://") or not title or not host:
                            continue
                        if required_host and host != required_host and not host.endswith("." + required_host):
                            continue
                        if host.endswith(("google.com", "bing.com")) or url in seen:
                            continue
                        ancestor = "xpath=ancestor::li[1]" if name == "Bing" else "xpath=ancestor::div[3]"
                        snippet = ""
                        try:
                            snippet = " ".join((item.locator(ancestor).text_content(timeout=1500) or "").split())[:650]
                        except Exception:
                            pass
                        seen.add(url)
                        results.append({"title": title[:180], "url": url, "snippet": snippet,
                                        "query": query, "engine": name})
                    if not results:
                        errors.append(f"{name}: 没有可用的自然搜索结果")
                except Exception as exc:
                    errors.append(f"{name}: {type(exc).__name__}")
            if results:
                terms = re.findall(r"[a-z]{3,}|[\u4e00-\u9fff]{2,}", query.casefold())
                results.sort(key=lambda hit: sum(term in (hit["title"] + " " + hit["snippet"]).casefold()
                                                for term in terms), reverse=True)
                return results[:limit]
            raise RuntimeError("浏览器搜索未返回可用结果（可能遇到验证码、地区限制或页面改版）：" + "; ".join(errors))
        finally:
            browser.close()
