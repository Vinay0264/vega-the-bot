"""
search.py — VEGA
Smart web search with BS4 filtering.
Type-based depth: quick (snippets only) vs deep (scrape + filter).
Hard cap: 400 words max sent to brain.py.
"""

import asyncio
import re
from ddgs import DDGS
from bs4 import BeautifulSoup

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False
    import urllib.request

# ── DDG snippet search ────────────────────────────────────────────────────────
def _ddg_search(query: str, max_results: int = 5) -> list:
    """Returns list of {title, body, href} dicts from DDG."""
    try:
        with DDGS() as ddgs:
            return list(ddgs.text(query, max_results=max_results))
    except Exception as e:
        print(f"[DDG error] {e}")
        return []

# ── Extract clean text from HTML ──────────────────────────────────────────────
def _extract_clean(html: str, max_words: int = 400) -> str:
    """
    BeautifulSoup clean extraction:
    - Remove all noise tags
    - Extract only first 3 meaningful paragraphs
    - Hard cap at max_words
    """
    try:
        soup = BeautifulSoup(html, "html.parser")
        # Remove all noise
        for tag in soup(['script','style','nav','footer','header',
                         'aside','form','iframe','ads','advertisement',
                         'cookie','popup','modal','sidebar','menu',
                         'noscript','svg','figure','figcaption']):
            tag.decompose()
        # Remove elements with noisy class/id names
        for tag in soup.find_all(True):
            cls = ' '.join(tag.get('class', []))
            iid = tag.get('id', '')
            if any(x in cls.lower() or x in iid.lower()
                   for x in ['nav','menu','footer','header','sidebar',
                              'ad','banner','cookie','popup','social']):
                tag.decompose()
        # Try to find main content area
        main = (soup.find('article') or
                soup.find('main') or
                soup.find(class_=re.compile(r'content|article|post|body', re.I)) or
                soup.find('body') or soup)
        # Extract paragraphs with real content
        paragraphs = []
        for p in main.find_all(['p','h1','h2','h3','li']):
            text = p.get_text(separator=' ').strip()
            # Skip short/noisy paragraphs
            if len(text.split()) < 8:
                continue
            if any(x in text.lower() for x in ['cookie','subscribe','newsletter',
                                                 'sign up','login','register',
                                                 'advertisement','click here']):
                continue
            paragraphs.append(text)
            if len(paragraphs) >= 4:  # max 4 paragraphs
                break
        combined = ' '.join(paragraphs)
        # Hard word cap
        words = combined.split()
        if len(words) > max_words:
            combined = ' '.join(words[:max_words])
        return combined.strip()
    except Exception as e:
        print(f"[BS4 error] {e}")
        return ""

# ── Fetch URL ─────────────────────────────────────────────────────────────────
async def _fetch_url(url: str, timeout: int = 6) -> str:
    """Fetch URL content. Returns empty string on failure."""
    try:
        if HAS_HTTPX:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                r = await client.get(url, headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                })
                return r.text
        else:
            # Fallback to urllib in executor
            def _fetch():
                import urllib.request
                req = urllib.request.Request(url, headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
                })
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    return resp.read().decode('utf-8', errors='ignore')
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, _fetch)
    except Exception as e:
        print(f"[Fetch error] {url[:60]} → {e}")
        return ""

# ── Quick search — DDG snippets only ─────────────────────────────────────────
async def _quick_search(query: str) -> str:
    """
    For factual one-liners: DDG snippets are usually enough.
    Returns clean text under 200 words. No scraping.
    """
    loop = asyncio.get_event_loop()
    results = await loop.run_in_executor(None, _ddg_search, query, 3)
    if not results:
        return ""
    lines = []
    word_count = 0
    for r in results:
        title = r.get('title','').strip()
        body  = BeautifulSoup(r.get('body',''), 'html.parser').get_text().strip()
        if body:
            chunk = f"{title}: {body}" if title else body
            words = chunk.split()
            if word_count + len(words) > 200:
                lines.append(' '.join(words[:max(0, 200-word_count)]))
                break
            lines.append(chunk)
            word_count += len(words)
    return '\n'.join(lines)

# ── Deep search — scrape top URLs ─────────────────────────────────────────────
async def _deep_search(query: str) -> str:
    """
    For temporal/complex queries: scrape top 2 pages + extract clean text.
    Hard cap 400 words total.
    """
    loop = asyncio.get_event_loop()
    results = await loop.run_in_executor(None, _ddg_search, query, 5)
    if not results:
        return ""

    # Start with DDG snippets as fallback
    snippet_text = await _quick_search(query)

    # Scrape top 2 URLs concurrently
    urls = [r.get('href','') for r in results if r.get('href','').startswith('http')][:2]
    if not urls:
        return snippet_text

    fetched = await asyncio.gather(*[_fetch_url(u) for u in urls], return_exceptions=True)

    all_text = []
    word_budget = 400

    for html in fetched:
        if isinstance(html, Exception) or not html:
            continue
        clean = _extract_clean(html, max_words=word_budget)
        if clean and len(clean.split()) > 15:
            all_text.append(clean)
            word_budget -= len(clean.split())
            if word_budget <= 50:
                break

    if all_text:
        combined = ' '.join(all_text)
        # Final hard cap
        words = combined.split()
        return ' '.join(words[:400])

    # Fallback to snippets
    return snippet_text

# ── Main entry point ──────────────────────────────────────────────────────────
async def smart_search(query: str, depth: str = 'quick') -> str:
    """
    depth='quick' → DDG snippets only (~200 words max)
    depth='deep'  → scrape + BS4 filter (~400 words max)
    """
    print(f"[Search] depth={depth} | {query[:60]}")
    if depth == 'deep':
        return await _deep_search(query)
    return await _quick_search(query)

# ── Legacy compatibility ──────────────────────────────────────────────────────
async def search_web(query: str, max_results: int = 5) -> str:
    """Keep backward compatibility with old server.py calls."""
    return await smart_search(query, depth='quick')