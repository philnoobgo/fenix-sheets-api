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

app = FastAPI(title="Fenix Sheets API", version="1.6.0")


def build_steamdt_search_url(item: str) -> str:
    return f"{STEAMDT_BASE}/mkt?search={urllib.parse.quote_plus(item)}"


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

    return browser, context.new_page()


def load_fenix(page):
    console_logs = []
    page_errors = []

    page.on("console", lambda msg: console_logs.append(f"{msg.type}: {msg.text}"))
    page.on("pageerror", lambda exc: page_errors.append(str(exc)))

    response = page.goto(FENIX_APP_URL, wait_until="domcontentloaded", timeout=120000)

    for _ in range(12):
        page.wait_for_timeout(10000)

        for frame in page.frames:
            try:
                text = frame.locator("body").inner_text(timeout=3000)
                inputs = frame.locator("input").count()
                buttons = frame.locator("button").count()

                if "FenixS" in text and inputs > 0 and buttons > 0:
                    return response, console_logs, page_errors
            except Exception:
                pass

    return response, console_logs, page_errors


def find_working_frame(page):
    # Prioriza o frame real do app FenixS
    for frame in page.frames:
        try:
            text = frame.locator("body").inner_text(timeout=3000)
            inputs = frame.locator("input").count()
            buttons = frame.locator("button").count()

            if "FenixS" in text and inputs > 0 and buttons > 0:
                return frame
        except Exception:
            pass

    # Segunda tentativa: qualquer frame com input
    for frame in page.frames:
        try:
            if frame.locator("input").count() > 0:
                return frame
        except Exception:
            pass

    return page.main_frame


def get_all_frame_debug(page):
    frames_debug = []

    for idx, frame in enumerate(page.frames):
        try:
            body_text = frame.locator("body").inner_text(timeout=5000)
        except Exception:
            body_text = ""

        try:
            html = frame.content()
        except Exception:
            html = ""

        try:
            inputs = frame.locator("input").count()
            textareas = frame.locator("textarea").count()
            buttons = frame.locator("button").count()
        except Exception:
            inputs = textareas = buttons = 0

        frames_debug.append({
            "index": idx,
            "url": frame.url,
            "inputs": inputs,
            "textareas": textareas,
            "buttons": buttons,
            "body_length": len(body_text),
            "html_length": len(html),
            "body_preview": body_text[:1500],
        })

    return frames_debug


def analyze_with_browser(item: str):
    steamdt_search_url = build_steamdt_search_url(item)
    fenix_input_url = build_fenix_input_url(item)

    with sync_playwright() as p:
        browser, page = create_browser_page(p)

        try:
            response, console_logs, page_errors = load_fenix(page)
            frame = find_working_frame(page)

            inputs = frame.locator("input").count()
            textareas = frame.locator("textarea").count()
            buttons = frame.locator("button").count()
            frame_text = frame.locator("body").inner_text(timeout=5000)

            if inputs > 0:
                field = frame.locator("input").first
            elif textareas > 0:
                field = frame.locator("textarea").first
            else:
                raise HTTPException(
                    status_code=502,
                    detail={
                        "erro": "Nenhum campo encontrado no frame correto.",
                        "status": response.status if response else None,
                        "frame_text": frame_text[:2000],
                        "frames": get_all_frame_debug(page),
                        "console_logs": console_logs[-30:],
                        "page_errors": page_errors[-20:],
                    },
                )

            field.fill(fenix_input_url)
            page.wait_for_timeout(1500)

            # Clica no botão Analyze. Primeiro tenta por texto, depois pelo botão após o campo.
            try:
                button = frame.get_by_role("button", name=re.compile("analyze|analisar", re.I)).first
                button.click(timeout=60000)
            except Exception:
                frame.locator("button").nth(1).click(timeout=60000)

            page.wait_for_timeout(50000)

            final_text = ""
            for fr in page.frames:
                try:
                    final_text += "\n" + fr.locator("body").inner_text(timeout=5000)
                except Exception:
                    pass

            supply, valuation_usd = extract_metrics_from_text(final_text)

            if supply is None or valuation_usd is None:
                raise HTTPException(
                    status_code=502,
                    detail={
                        "erro": "Não consegui ler Est. Supply ou Market Valuation.",
                        "preview_texto": final_text[:3500],
                        "frames": get_all_frame_debug(page),
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
                "source": "fenix_engine_browser_iframe",
            }

        except PlaywrightTimeoutError as e:
            raise HTTPException(status_code=504, detail=f"Timeout ao consultar o Fenix Engine: {e}")

        finally:
            browser.close()


@app.get("/")
def home():
    return {"ok": True, "service": "Fenix Sheets API", "version": "1.6.0"}


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/debug")
def debug():
    with sync_playwright() as p:
        browser, page = create_browser_page(p)

        try:
            response, console_logs, page_errors = load_fenix(page)

            return {
                "fenix_url": FENIX_APP_URL,
                "final_url": page.url,
                "status": response.status if response else None,
                "title": page.title(),
                "total_frames": len(page.frames),
                "frames": get_all_frame_debug(page),
                "console_logs": console_logs[-30:],
                "page_errors": page_errors[-20:],
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

    return JSONResponse(analyze_with_browser(item))
