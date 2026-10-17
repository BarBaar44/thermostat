#!/usr/bin/env python3
"""Create a Cloudless Multiroom Thermostat entry with all heating_sim rooms.

Runs the real config flow and room flows through Home Assistant's REST API,
so it also tests the forms. Standard library only.

  python3 setup_heating.py --token <long-lived token>
  python3 setup_heating.py --token <token> --source switch   # relay boiler
  python3 setup_heating.py --token <token> --url http://host:8124

On a fresh Home Assistant (onboarding not done yet) it can do onboarding
first, creating the owner account, and then use that login:

  python3 setup_heating.py --onboard --username test --password testtest
  python3 setup_heating.py --onboard --wait 900 ...   # wait for HA to come up
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

DOMAIN = "cloudless_multiroom_thermostat"

# Must match heating_sim in config/configuration.yaml
ROOMS = [
    {"name": "Living Room", "trvs": 3, "window": "input_boolean.sim_living_room_garden_door", "warm": 19.0},
    {"name": "Office", "trvs": 1, "window": None, "warm": 19.5},
    {"name": "Sports Room", "trvs": 1, "window": None, "warm": 16.0},
    {"name": "Master Bedroom", "trvs": 2, "window": "input_boolean.sim_master_bedroom_window", "warm": 17.0},
    {"name": "Attic", "trvs": 0, "window": None, "warm": 16.0},
]

# Local test HA only: never route through a system proxy
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def slug(name: str) -> str:
    return name.lower().replace(" ", "_")


class Api:
    def __init__(self, url: str, token: str | None = None) -> None:
        self.url = url.rstrip("/")
        self.token = token

    def call(self, method: str, path: str, body: dict | None = None, form: bool = False):
        headers = {"Content-Type": "application/x-www-form-urlencoded" if form else "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        data = None
        if body is not None:
            data = urllib.parse.urlencode(body).encode() if form else json.dumps(body).encode()
        req = urllib.request.Request(self.url + path, method=method, data=data, headers=headers)
        try:
            with _OPENER.open(req, timeout=30) as resp:
                return json.loads(resp.read() or b"null")
        except urllib.error.HTTPError as err:
            sys.exit(f"{method} {path} failed: {err.code} {err.read().decode(errors='replace')}")
        except urllib.error.URLError as err:
            sys.exit(f"Cannot reach {self.url}: {err.reason}")

    def step(self, base: str, flow_id: str, data: dict) -> dict:
        result = self.call("POST", f"{base}/{flow_id}", data)
        if result.get("errors"):
            sys.exit(f"Form errors at step {result.get('step_id')}: {result['errors']}")
        return result


def wait_for(url: str, seconds: int) -> None:
    """Wait until HA answers /api/onboarding (no auth needed)."""
    deadline = time.monotonic() + seconds
    while True:
        try:
            with _OPENER.open(url.rstrip("/") + "/api/onboarding", timeout=5) as resp:
                if resp.status == 200:
                    return
        except (urllib.error.URLError, OSError):
            pass
        if time.monotonic() > deadline:
            sys.exit(f"Home Assistant at {url} did not come up within {seconds} s")
        time.sleep(5)


def onboard(api: Api, username: str, password: str) -> None:
    """Finish every onboarding step and log in as the new owner."""
    pending = {s["step"] for s in api.call("GET", "/api/onboarding") if not s["done"]}
    if "user" not in pending:
        sys.exit("Onboarding was already done: use --token with a long-lived access token instead.")
    client_id = api.url + "/"
    user = api.call(
        "POST",
        "/api/onboarding/users",
        {"client_id": client_id, "name": username.title(), "username": username, "password": password, "language": "en"},
    )
    token = api.call(
        "POST",
        "/auth/token",
        {"grant_type": "authorization_code", "code": user["auth_code"], "client_id": client_id},
        form=True,
    )
    api.token = token["access_token"]
    if "core_config" in pending:
        api.call("POST", "/api/onboarding/core_config", {})
    if "analytics" in pending:
        api.call("POST", "/api/onboarding/analytics", {})
    if "integration" in pending:
        api.call("POST", "/api/onboarding/integration", {"client_id": client_id, "redirect_uri": client_id + "?auth_callback=1"})
    print(f"Onboarding done, owner '{username}' created")


def room_data(room: dict) -> dict:
    key = slug(room["name"])
    count = room["trvs"]
    return {
        "name": room["name"],
        "temperature_sensor": f"sensor.sim_{key}_temperature",
        "trvs": [f"climate.sim_{key}_trv_{i}" for i in range(1, count + 1)],
        "external_temperature_inputs": [f"number.sim_{key}_trv_{i}_external_temperature" for i in range(1, count + 1)],
        "window_sensors": [room["window"]] if room["window"] else [],
        "window_open_delay": 30,
        "window_close_delay": 15,
        "eco_temperature": 15.0,
        "warm_temperature": room["warm"],
        "overshoot_buffer": 1.0,
        "min_temp": 5.0,
        "max_temp": 25.0,
        "target_temp_step": "0.5",
    }


def create_heating(api: Api, source: str) -> None:
    existing = [e for e in api.call("GET", "/api/config/config_entries/entry") if e["domain"] == DOMAIN]
    if existing:
        sys.exit(
            f"A {DOMAIN} entry already exists ({existing[0]['title']}). "
            "Delete it in Settings > Devices & services first."
        )

    flow = "/api/config/config_entries/flow"
    result = api.call("POST", flow, {"handler": DOMAIN})
    fid = result["flow_id"]
    title = "Sim heating (relay)" if source == "switch" else "Sim heating"
    api.step(flow, fid, {"name": title, "heat_source_type": source})
    if source == "switch":
        api.step(flow, fid, {"heat_source_entity": "switch.sim_boiler_relay", "hysteresis": 0.3})
    else:
        api.step(flow, fid, {"heat_source_entity": "climate.sim_boiler", "ch_switch": "switch.sim_ch_enabled"})
    result = api.step(
        flow,
        fid,
        {
            "off_temperature": 10,
            "presence_entity": "input_boolean.sim_somebody_home",
            "away_mode": "off",
            "skip_unchanged_writes": True,
            "external_temp_heartbeat": 60,
        },
    )
    if result.get("type") != "create_entry":
        sys.exit(f"Config flow did not finish: {result}")
    entry_id = result["result"]["entry_id"]
    print(f"Created '{title}' ({entry_id})")

    sub = "/api/config/config_entries/subentries/flow"
    for room in ROOMS:
        start = api.call("POST", sub, {"handler": [entry_id, "room"]})
        done = api.step(sub, start["flow_id"], room_data(room))
        if done.get("type") != "create_entry":
            sys.exit(f"Room {room['name']} did not finish: {done}")
        print(f"  added room {room['name']} ({room['trvs']} TRVs)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="http://localhost:8124")
    parser.add_argument("--token", help="Long-lived access token (Profile > Security)")
    parser.add_argument("--onboard", action="store_true", help="Do onboarding first on a fresh HA")
    parser.add_argument("--username", default="test")
    parser.add_argument("--password", default="testtest")
    parser.add_argument("--wait", type=int, default=0, help="Seconds to wait for HA to come up")
    parser.add_argument("--source", choices=["climate", "switch"], default="climate")
    args = parser.parse_args()
    if not args.token and not args.onboard:
        parser.error("give --token, or --onboard on a fresh Home Assistant")

    if args.wait:
        wait_for(args.url, args.wait)
    api = Api(args.url, args.token)
    if args.onboard:
        onboard(api, args.username, args.password)
    create_heating(api, args.source)
    print("Done. Set rooms to heat in the UI.")


if __name__ == "__main__":
    main()
