"""Tell Franyeska on monday.com that a shop's chart is ready for the QBO journal entries.

Posts "Chart is ready for JE @Franyeska Reyes" on the "Enter parts adjustments JEs" item in
the group for the current month on that shop's accounting board (as Julio does by hand).

    .venv/bin/python monday.py --shop davie    # post now (after answers were applied by hand)
"""
import argparse
import json
from datetime import datetime

import requests

from config import MONDAY_FRANYESKA, MONDAY_TOKEN_FILE, SHOPS, TZ

API = "https://api.monday.com/v2"
ITEM_NAME = "Enter parts adjustments JEs"


def _gql(query, variables=None):
    r = requests.post(API, json={"query": query, "variables": variables or {}}, timeout=60,
                      headers={"API-Version": "2024-10", "Authorization": MONDAY_TOKEN_FILE.read_text().strip()})
    r.raise_for_status()
    out = r.json()
    if out.get("errors"):
        raise RuntimeError(f"monday API: {out['errors']}")
    return out["data"]


def find_item(shop, when=None):
    """This month's 'Enter parts adjustments JEs' item on the shop's board."""
    month = (when or datetime.now(TZ)).strftime("%B").lower()
    data = _gql("""query ($b: [ID!]) { boards(ids: $b) { items_page(limit: 100, query_params: {rules:
        [{column_id: "name", compare_value: [$n], operator: contains_text}]}) { items { id name group { title } } } } }"""
                .replace("$n", json.dumps(ITEM_NAME)), {"b": [SHOPS[shop]["monday_board"]]})
    items = [i for i in data["boards"][0]["items_page"]["items"]
             if i["name"].strip() == ITEM_NAME and month in i["group"]["title"].lower()]
    if len(items) != 1:
        raise RuntimeError(f"expected one '{ITEM_NAME}' item in the {month.title()} group, found {len(items)}")
    return items[0]


def post_ready(shop):
    item = find_item(shop)
    body = "<p>Chart is ready for JE</p>"   # monday appends the @Franyeska Reyes mention from mentions_list
    mention = f'[{{id: {MONDAY_FRANYESKA}, type: User}}]'
    _gql(f"mutation ($i: ID!, $body: String!) {{ create_update(item_id: $i, body: $body, mentions_list: {mention}) {{ id }} }}",
         {"i": item["id"], "body": body})
    return f"https://tuneriteauto.monday.com/boards/{SHOPS[shop]['monday_board']}/pulses/{item['id']}"


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--shop", required=True, choices=SHOPS)
    print(post_ready(ap.parse_args().shop))
