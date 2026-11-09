"""End-to-end check without a browser: does what frontend/js/session.js does,
over the same raw Live WebSocket, with typed turns instead of a microphone.

    python scripts/live_smoke.py "İstanbul'da hava nasıl?"
    python scripts/live_smoke.py "Bilgisayarı kapat" "hayır"

Each argument is one user turn. Tool calls go to the running backend
(default http://127.0.0.1:8100), which really executes them, so only pass
commands you are happy to run. Mario's spoken replies are printed from the
output transcription.
"""

import argparse
import asyncio
import json
import sys
import time

import httpx
import websockets

WS = "wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContentConstrained"


async def turn(ws, api: httpx.AsyncClient, text: str, state: dict) -> None:
    print(f"\nSEN   : {text}")
    state["heard"].append({"text": text, "at": time.monotonic()})
    await ws.send(json.dumps({"realtimeInput": {"text": text}}))
    said = ""
    answered_tool = False  # the spoken reply comes in a new turn after the toolResponse
    while True:
        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=60))
        if calls := msg.get("toolCall", {}).get("functionCalls"):
            responses = []
            for fc in calls:
                utterances = []
                if fc["name"] == "confirm_action":
                    utterances = [h["text"] for h in state["heard"] if h["at"] >= state["mark"]]
                res = (
                    await api.post(
                        "/api/tool",
                        json={
                            "name": fc["name"],
                            "args": fc.get("args", {}),
                            "utterances": utterances,
                        },
                    )
                ).json()
                print(
                    f"ARAÇ  : {fc['name']}({json.dumps(fc.get('args', {}), ensure_ascii=False)}) -> "
                    f"{res.get('status')}: {res.get('message')}"
                )
                if res.get("status") == "needs_confirmation":
                    state["mark"] = time.monotonic()
                responses.append({"id": fc["id"], "name": fc["name"], "response": res})
            await ws.send(json.dumps({"toolResponse": {"functionResponses": responses}}))
            answered_tool = True
        sc = msg.get("serverContent", {})
        said += sc.get("outputTranscription", {}).get("text", "")
        if sc.get("turnComplete"):
            if answered_tool and not said.strip():
                answered_tool = False
                continue
            print(f"MARIO : {said.strip()}")
            return


async def main(turns: list[str], base: str, code: str) -> None:
    headers = {"X-Access-Code": code} if code else {}
    async with httpx.AsyncClient(base_url=base, headers=headers, timeout=60) as api:
        tok = (await api.post("/api/token")).raise_for_status().json()
        async with websockets.connect(f"{WS}?access_token={tok['token']}", max_size=None) as ws:
            await ws.send(json.dumps({"setup": {"model": f"models/{tok['model']}"}}))
            while "setupComplete" not in json.loads(await ws.recv()):
                pass
            state = {"heard": [], "mark": 0.0}
            for text in turns:
                await turn(ws, api, text, state)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser()
    p.add_argument("turns", nargs="+")
    p.add_argument("--base", default="http://127.0.0.1:8100")
    p.add_argument("--code", default="")
    a = p.parse_args()
    asyncio.run(main(a.turns, a.base, a.code))
