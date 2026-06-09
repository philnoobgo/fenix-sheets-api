import os
import re
import urllib.parse
from typing import Optional

import requests
from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

APP_TOKEN = os.getenv("FENIX_TOKEN", "troque-este-token")
FENIX_APP_URL = os.getenv("FENIX_APP_URL", "https://cs2item-calculator-by-fenixs.streamlit.app/")
STEAMDT_BASE = "https://www.steamdt.com/en"

app = FastAPI(title="Fenix Sheets API", version="1.4.0")


def build_steamdt_search_url(item: str) -> str:
    encoded = urllib.parse.quote_plus(item)
    return f"{STEAMDT_BASE}/mkt?search={encoded}"


def build_fenix_input_url(item: str) -> str:
    return f"https://www.steamdt.com/en/cs2/{urllib.parse.quote(item, safe='')}"


def get_usd_brl() -> float:
    try:
        r = requests.get("https://economia.awesomeapi.com.br/json/last/USD-BRL", timeout=10)
        r.raise_for_status()
        return float(r.json()["USDBRL"]["bid"])
    except Exception:
        return float(os.getenv("USD_BRL_FALLBACK", "5.40"))


def money_to_float(text: str) -> Optional[float]:
    cleaned = re.sub(r"[^0-9.,]", "", text or "")
    if not cleaned:
        return None

    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(",", "")
    elif "," in cleaned and "." not in cleaned:
        cleaned = cleaned.replace(",", ".")

    try:
        return float(cleaned)
    except ValueError:
        return None


def extract_metrics_from_text(text: str):
    supply = None
    valuation_usd = None

    m_supply = re.search(r"EST\.?\s*SUPPLY\s*([0-9,.]+)", text, flags=re.I)
    if m_supply:
        supply = int(float(m_supply.group(1).replace(",", "")))

    m_val = re.search(r"MARKET\s+VALUATION\s*\$?\s*([0-9,.]+)", text, flags=re.I)
    if m_val:
        valuation_usd = money_to_float(m_val.group(1))

    return supply, valuation_usd


def create_browser_page(p):
    browser = p.chromium.launch(
        headless=True,
        args=[
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-setuid-sandbox",
            "--disable-features=IsolateOrigins,site-per-process",
            "--disable-blink-features=AutomationControlled",
            "--window-size=1600,1000",
        ],
    )

    context = browser.new_context(
        viewport={"width": 1600, "height": 1000},
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        java_script_enabled=True,
        ignore_https_errors=True,
    )

    page = context.new_page()
    return browser, page


def load_fenix(page):
    console_logs = []
    page_errors = []

    page.on("console", lambda msg: console_logs.append(f"{msg.type}: {msg.text}"))
    page.on("pageerror", lambda exc: page_errors.append(str(exc)))

    response = page.goto(FENIX_APP_URL, wait_until="domcontentloaded", timeout=120000)

    for _ in range(6):
        page.wait_for_timeout(10000)
        inputs = page.locator("input").count()
        textareas = page.locator("textarea").count()
        buttons = page.locator("button").count()
        body_text = page.locator("body").inner_text(timeout=10000)

        if inputs > 0 or textareas > 0 or buttons > 0 or len(body_text.strip()) > 20:
            break

    html = page.content()
    body_text = page.locator("body").inner_text(timeout=30000)

    return {
        "response": response,
        "html": html,
        "body_text": body_text,
        "console_logs": console_logs[-30:],
        "page_errors": page_errors[-20:],
    }


def analyze_with_browser(item: str):
    steamdt_search_url = build_steamdt_search_url(item)
    fenix_input_url = build_fenix_input_url(item)

    with sync_playwright() as p:
        browser, page = create_browser_page(p)

        try:
            loaded = load_fenix(page)

            inputs = page.locator("input").count()
            textareas = page.locator("textarea").count()
            buttons = page.locator("button").count()

            if inputs > 0:
                field = page.locator("input").first
            elif textareas > 0:
                field = page.locator("textarea").first
            else:
                raise HTTPException(
                    status_code=502,
                    detail={
                        "erro": "O Streamlit abriu, mas não renderizou o campo de busca.",
                        "status": loaded["response"].status if loaded["response"] else None,
                        "title": page.title(),
                        "inputs": inputs,
                        "textareas": textareas,
                        "buttons": buttons,
                        "body_length": len(loaded["body_text"]),
                        "html_length": len(loaded["html"]),
                        "body_preview": loaded["body_text"][:2000],
                        "console_logs": loaded["console_logs"],
                        "page_errors": loaded["page_errors"],
                    },
                )

            field.wait_for(state="visible", timeout=120000)
            field.fill(fenix_input_url)

            page.wait_for_timeout(1500)

            try:
                button = page.get_by_role("button", name=re.compile("analyze|analisar", re.I)).first
                button.click(timeout=60000)
            except Exception:
                page.locator("button").last.click(timeout=60000)

            page.wait_for_timeout(45000)

            final_text = page.locator("body").inner_text(timeout=60000)
            supply, valuation_usd = extract_metrics_from_text(final_text)

            if supply is None or valuation_usd is None:
                raise HTTPException(
                    status_code=502,
                    detail={
                        "erro": "Não consegui ler Est. Supply ou Market Valuation.",
                        "preview_texto": final_text[:3000],
                    },
                )

            usd_brl = get_usd_brl()

            return {
                "item": item,
                "est_supply": supply,
                "market_valuation_usd": valuation_usd,
                "usd_brl": usd_brl,
                "market_valuation_brl": round(valuation_usd * usd_brl, 2),
                "steamdt_url": steamdt_search_url,
                "fenix_url": FENIX_APP_URL,
                "fenix_input_url": fenix_input_url,
                "source": "fenix_engine_browser",
            }

        except PlaywrightTimeoutError as e:
            raise HTTPException(status_code=504, detail=f"Timeout ao consultar o Fenix Engine: {e}")

        finally:
            browser.close()


@app.get("/")
def home():
    return {
        "ok": True,
        "service": "Fenix Sheets API",
        "version": "1.4.0",
    }


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/debug")
def debug():
    with sync_playwright() as p:
        browser, page = create_browser_page(p)

        try:
            loaded = load_fenix(page)

            return {
                "fenix_url": FENIX_APP_URL,
                "final_url": page.url,
                "status": loaded["response"].status if loaded["response"] else None,
                "title": page.title(),
                "html_length": len(loaded["html"]),
                "body_length": len(loaded["body_text"]),
                "inputs": page.locator("input").count(),
                "textareas": page.locator("textarea").count(),
                "buttons": page.locator("button").count(),
                "body_preview": loaded["body_text"][:3000],
                "console_logs": loaded["console_logs"],
                "page_errors": loaded["page_errors"],
            }

        finally:
            browser.close()


@app.get("/analyze")
def analyze(
    item: str = Query(..., min_length=3),
    x_fenix_token: str = Header(default=""),
):
    if x_fenix_token != APP_TOKEN:
        raise HTTPException(status_code=401, detail="Token inválido")

    result = analyze_with_browser(item)
    return JSONResponse(result)
