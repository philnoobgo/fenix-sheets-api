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

app = FastAPI(title="Fenix Sheets API", version="1.3.0")


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


def open_fenix_page(page):
    response = page.goto(
        FENIX_APP_URL,
        wait_until="load",
        timeout=120000,
    )

    page.wait_for_timeout(45000)

    html = page.content()
    body_text = page.locator("body").inner_text(timeout=30000)

    return response, html, body_text


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
                "--disable-blink-features=AutomationControlled",
            ],
        )

        page = browser.new_page(
            viewport={"width": 1600, "height": 1000},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
        )

        try:
            response, html, body_text = open_fenix_page(page)

            total_inputs = page.locator("input").count()
            total_textareas = page.locator("textarea").count()
            total_buttons = page.locator("button").count()

            if total_inputs > 0:
                field = page.locator("input").first
            elif total_textareas > 0:
                field = page.locator("textarea").first
            else:
                raise HTTPException(
                    status_code=502,
                    detail={
                        "erro": "Nenhum input ou textarea encontrado no Fenix Engine.",
                        "status": response.status if response else None,
                        "final_url": page.url,
                        "title": page.title(),
                        "html_length": len(html),
                        "body_length": len(body_text),
                        "inputs": total_inputs,
                        "textareas": total_textareas,
                        "buttons": total_buttons,
                        "html_preview": html[:2000],
                        "body_preview": body_text[:2000],
                    },
                )

            field.wait_for(state="visible", timeout=120000)
            field.fill(fenix_input_url)

            page.wait_for_timeout(1000)

            try:
                button = page.get_by_role("button", name=re.compile("analyze|analisar", re.I)).first
                button.click(timeout=60000)
            except Exception:
                button = page.locator("button").last
                button.click(timeout=60000)

            page.wait_for_timeout(35000)

            final_text = page.locator("body").inner_text(timeout=60000)

            supply, valuation_usd = extract_metrics_from_text(final_text)

            if supply is None or valuation_usd is None:
                raise HTTPException(
                    status_code=502,
                    detail={
                        "erro": "Não consegui ler Est. Supply ou Market Valuation.",
                        "preview_texto": final_text[:2500],
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


@app.get("/")
def home():
    return {
        "ok": True,
        "service": "Fenix Sheets API",
        "version": "1.3.0",
        "routes": [
            "/health",
            "/debug",
            "/analyze?item=Sticker%20%7C%20FalleN%20(Holo)%20%7C%20Cologne%202026",
        ],
    }


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/debug")
def debug():
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-blink-features=AutomationControlled",
            ],
        )

        page = browser.new_page(
            viewport={"width": 1600, "height": 1000},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
        )

        try:
            response, html, body_text = open_fenix_page(page)

            return {
                "fenix_url": FENIX_APP_URL,
                "final_url": page.url,
                "status": response.status if response else None,
                "title": page.title(),
                "html_length": len(html),
                "body_length": len(body_text),
                "inputs": page.locator("input").count(),
                "textareas": page.locator("textarea").count(),
                "buttons": page.locator("button").count(),
                "html_preview": html[:3000],
                "body_preview": body_text[:3000],
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
