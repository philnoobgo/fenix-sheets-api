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

app = FastAPI(title="Fenix Sheets API", version="1.1.0")


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
    if not text:
        return None

    cleaned = re.sub(r"[^0-9.,]", "", text)

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


def analyze_with_browser(item: str):
    steamdt_search_url = build_steamdt_search_url(item)
    fenix_input_url = build_fenix_input_url(item)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ],
        )

        page = browser.new_page(viewport={"width": 1600, "height": 1000})

        try:
            page.goto(
                FENIX_APP_URL,
                wait_until="domcontentloaded",
                timeout=90000,
            )

            page.wait_for_timeout(10000)

            input_box = page.locator('input[type="text"]').first
            input_box.wait_for(state="visible", timeout=90000)
            input_box.fill(fenix_input_url)

            page.wait_for_timeout(1000)

            analyze_button = page.get_by_text("ANALYZE", exact=False).first
            analyze_button.click(timeout=60000)

            page.wait_for_timeout(30000)

            body_text = page.locator("body").inner_text(timeout=60000)

            supply, valuation_usd = extract_metrics_from_text(body_text)

            if supply is None or valuation_usd is None:
                raise HTTPException(
                    status_code=502,
                    detail={
                        "erro": "Não consegui ler Est. Supply ou Market Valuation.",
                        "preview_texto": body_text[:2000],
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
            raise HTTPException(
                status_code=504,
                detail=f"Timeout ao consultar o Fenix Engine: {e}",
            )

        finally:
            browser.close()


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/analyze")
def analyze(
    item: str = Query(..., min_length=3),
    x_fenix_token: str = Header(default=""),
):
    if x_fenix_token != APP_TOKEN:
        raise HTTPException(status_code=401, detail="Token inválido")

    result = analyze_with_browser(item)
    return JSONResponse(result)
