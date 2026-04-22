"""
Web search — synchronous wrappers for Tavily (primary), Brave (secondary),
wttr.in weather (zero-config weather fallback), and DuckDuckGo instant answers.

Keys are read from environment variables:
  TAVILY_API_KEY       — tavily.com (best, full web results)
  BRAVE_SEARCH_API_KEY — api.search.brave.com
  (neither required)   — wttr.in for weather / DDG for instant answers
"""
from __future__ import annotations

import os
import re as _re
import urllib.parse

import httpx

_MAX_RESULTS = 5
_TIMEOUT = 15


def _tavily(query: str, max_results: int) -> list[dict]:
    api_key = os.environ.get("TAVILY_API_KEY", "")
    if not api_key:
        return []
    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.post(
                "https://api.tavily.com/search",
                json={"api_key": api_key, "query": query, "max_results": max_results},
            )
            resp.raise_for_status()
            return resp.json().get("results", [])
    except Exception:
        return []


def _brave(query: str, max_results: int) -> list[dict]:
    api_key = os.environ.get("BRAVE_SEARCH_API_KEY", "")
    if not api_key:
        return []
    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": query, "count": max_results},
                headers={
                    "Accept": "application/json",
                    "X-Subscription-Token": api_key,
                },
            )
            resp.raise_for_status()
            raw = resp.json().get("web", {}).get("results", [])
            return [
                {
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "content": r.get("description", ""),
                }
                for r in raw
            ]
    except Exception:
        return []


_WEATHER_RE = _re.compile(
    r"\b(weather|temperature|temp|forecast|rain|snow|humidity|wind|hot|cold|sunny|cloudy|celsius|fahrenheit|degrees)\b",
    _re.IGNORECASE,
)

_STRIP_WORDS_RE = _re.compile(
    r"\b(weather|temperature|temp|forecast|today|tomorrow|current|now|outside|degrees?|celsius|fahrenheit)\b",
    _re.IGNORECASE,
)


def _extract_location(query: str) -> str:
    """Best-effort city extraction from a weather query."""
    loc = _STRIP_WORDS_RE.sub("", query)
    loc = _re.sub(r"\b(what|is|the|in|for|at|near|right|now|like|how)\b", "", loc, flags=_re.IGNORECASE)
    loc = _re.sub(r"[?!.,]", "", loc)
    loc = _re.sub(r"\s+", " ", loc).strip()
    return loc


def _wttr_weather(query: str) -> list[dict]:
    """
    Live weather from wttr.in — completely free, no API key required.
    Only called when the query looks like a weather question.
    """
    if not _WEATHER_RE.search(query):
        return []

    location = _extract_location(query)

    try:
        url = f"https://wttr.in/{urllib.parse.quote(location)}"
        with httpx.Client(timeout=_TIMEOUT, follow_redirects=True) as client:
            resp = client.get(
                url,
                params={"format": "j1"},  # structured JSON
                headers={"User-Agent": "curl/7.68.0"},  # wttr.in prefers curl UA
            )
            resp.raise_for_status()
            data = resp.json()

        current = data["current_condition"][0]
        area = data.get("nearest_area", [{}])[0]
        city = area.get("areaName", [{}])[0].get("value", location or "your location")
        country = area.get("country", [{}])[0].get("value", "")
        region = area.get("region", [{}])[0].get("value", "")

        temp_c = current.get("temp_C", "?")
        temp_f = current.get("temp_F", "?")
        feels_c = current.get("FeelsLikeC", "?")
        feels_f = current.get("FeelsLikeF", "?")
        desc = current.get("weatherDesc", [{}])[0].get("value", "")
        humidity = current.get("humidity", "?")
        wind_kmph = current.get("windspeedKmph", "?")
        wind_dir = current.get("winddir16Point", "")

        place = ", ".join(filter(None, [city, region, country]))
        summary = (
            f"{place}: {desc}, {temp_c}°C / {temp_f}°F "
            f"(feels like {feels_c}°C / {feels_f}°F), "
            f"humidity {humidity}%, wind {wind_kmph} km/h {wind_dir}"
        )

        return [{
            "title": f"Current weather — {place}",
            "url": f"https://wttr.in/{urllib.parse.quote(location)}",
            "content": summary,
        }]
    except Exception:
        return []


def _duckduckgo(query: str, max_results: int) -> list[dict]:
    """
    DuckDuckGo instant answers — no API key required.
    Returns structured instant-answer results when available; empty list otherwise.
    """
    try:
        with httpx.Client(timeout=_TIMEOUT, follow_redirects=True) as client:
            resp = client.get(
                "https://api.duckduckgo.com/",
                params={
                    "q": query,
                    "format": "json",
                    "no_html": "1",
                    "skip_disambig": "1",
                },
                headers={"User-Agent": "kairo/1.0"},
            )
            resp.raise_for_status()
            data = resp.json()

        results: list[dict] = []

        # Primary instant answer
        abstract = data.get("AbstractText", "").strip()
        if abstract:
            results.append({
                "title": data.get("Heading", query),
                "url": data.get("AbstractURL", ""),
                "content": abstract[:600],
            })

        # Answer (e.g. "15°C" for weather queries)
        answer = data.get("Answer", "").strip()
        if answer and not any(r["content"] == answer for r in results):
            results.append({
                "title": f"Answer: {answer[:80]}",
                "url": data.get("AbstractURL", ""),
                "content": answer[:600],
            })

        # Related topics
        for topic in data.get("RelatedTopics", []):
            if len(results) >= max_results:
                break
            if not isinstance(topic, dict):
                continue
            text = topic.get("Text", "").strip()
            if text:
                results.append({
                    "title": text[:80],
                    "url": topic.get("FirstURL", ""),
                    "content": text[:400],
                })

        return results[:max_results]
    except Exception:
        return []


def web_search(query: str, max_results: int = _MAX_RESULTS) -> str:
    """
    Search the web.
    Priority: Tavily → Brave → wttr.in (weather) → DuckDuckGo instant answers.
    wttr.in and DuckDuckGo require no API key.
    """
    results = (
        _tavily(query, max_results)
        or _brave(query, max_results)
        or _wttr_weather(query)
        or _duckduckgo(query, max_results)
    )
    if not results:
        return (
            "No search results returned for this query. "
            "For full web search set TAVILY_API_KEY or BRAVE_SEARCH_API_KEY. "
            f"Query: {query}"
        )
    return "\n\n".join(
        f"[{i + 1}] {r.get('title', '')}\n{r.get('url', '')}\n{r.get('content', r.get('snippet', ''))}"
        for i, r in enumerate(results)
    )
