import os
import json
import time
import datetime
import requests
import xml.etree.ElementTree as ET
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE_DIR, '.env'))

ENDED_STATE_PATH = os.path.join(BASE_DIR, 'data', 'ended_listings.json')

EBAY_APP_ID     = os.environ.get('EBAY_APP_ID')
EBAY_DEV_ID     = os.environ.get('EBAY_DEV_ID')
EBAY_CERT_ID    = os.environ.get('EBAY_CERT_ID')
EBAY_USER_TOKEN = os.environ.get('EBAY_USER_TOKEN')
EBAY_API_URL    = 'https://api.ebay.com/ws/api.dll'

# eBay accounts to sync, all under the same developer keyset (EBAY_APP_ID/DEV_ID/CERT_ID).
# Add more (name, env var) pairs here as additional stores are authorized.
EBAY_ACCOUNTS = [
    # ('saleswbrteam', EBAY_USER_TOKEN),  # disabled — syncing to wheelstires only
    ('wheelstires', os.environ.get('EBAY_USER_TOKEN_WHEELSTIRES')),
]

SHOPIFY_STORE_URL    = os.environ.get('SHOPIFY_STORE_URL')
SHOPIFY_ACCESS_TOKEN = os.environ.get('SHOPIFY_ACCESS_TOKEN')

NS = {'e': 'urn:ebay:apis:eBLBaseComponents'}


# eBay Trading API SiteID codes referenced by this script (there are more; add as needed).
SITE_NAME_TO_ID = {'US': '0', 'eBayMotors': '100'}


def ebay_call(call_name, body_xml, user_token=EBAY_USER_TOKEN, site_id='0'):
    headers = {
        'X-EBAY-API-SITEID': site_id,
        'X-EBAY-API-COMPATIBILITY-LEVEL': '967',
        'X-EBAY-API-CALL-NAME': call_name,
        'X-EBAY-API-APP-NAME': EBAY_APP_ID,
        'X-EBAY-API-DEV-NAME': EBAY_DEV_ID,
        'X-EBAY-API-CERT-NAME': EBAY_CERT_ID,
        'Content-Type': 'text/xml',
    }
    xml = f"""<?xml version="1.0" encoding="utf-8"?>
<{call_name}Request xmlns="urn:ebay:apis:eBLBaseComponents">
  <RequesterCredentials>
    <eBayAuthToken>{user_token}</eBayAuthToken>
  </RequesterCredentials>
  {body_xml}
</{call_name}Request>"""
    while True:
        try:
            r = requests.post(EBAY_API_URL, headers=headers, data=xml.encode('utf-8'), timeout=30)
            r.raise_for_status()
            return ET.fromstring(r.content)
        except Exception as e:
            print(f"eBay API error ({call_name}): {e}, retrying...", flush=True)
            time.sleep(5)


def get_ebay_listings(user_token=EBAY_USER_TOKEN):
    """Return dict of item_id -> {'skus': [sku, ...], 'is_variation': bool} for all active listings.
    Uses GetSellerList+Fine to get variation-level SKUs.
    GTC listings always end within 30 days, so EndTimeTo=now+32 captures all.
    """
    items = {}  # item_id -> {'skus': [...], 'is_variation': bool}
    page = 1
    now = datetime.datetime.now(datetime.timezone.utc)
    end_from = now.strftime('%Y-%m-%dT%H:%M:%S.000Z')
    end_to = (now + datetime.timedelta(days=32)).strftime('%Y-%m-%dT%H:%M:%S.000Z')

    while True:
        root = ebay_call('GetSellerList', f"""
  <EndTimeFrom>{end_from}</EndTimeFrom>
  <EndTimeTo>{end_to}</EndTimeTo>
  <GranularityLevel>Fine</GranularityLevel>
  <Pagination>
    <PageNumber>{page}</PageNumber>
    <EntriesPerPage>200</EntriesPerPage>
  </Pagination>
""", user_token=user_token)
        ack = root.findtext('e:Ack', '', NS)
        if ack == 'Failure':
            for err in root.findall('.//e:Errors', NS):
                print(f"eBay error: {err.findtext('e:LongMessage', '', NS)}", flush=True)
            break

        for item in root.findall('.//e:ItemArray/e:Item', NS):
            item_id = item.findtext('e:ItemID', '', NS).strip()
            if not item_id:
                continue
            skus = []
            variations = item.findall('.//e:Variations/e:Variation', NS)
            if variations:
                for var in variations:
                    sku = var.findtext('e:SKU', '', NS).strip()
                    if sku:
                        skus.append(sku)
            else:
                sku = item.findtext('e:SKU', '', NS).strip()
                if sku:
                    skus.append(sku)
            if skus:
                items[item_id] = {'skus': skus, 'is_variation': bool(variations)}

        has_more = root.findtext('.//e:HasMoreItems', 'false', NS)
        if has_more.lower() != 'true':
            break
        page += 1
        time.sleep(0.5)

    total_skus = sum(len(v['skus']) for v in items.values())
    print(f"eBay active listings: {len(items):,} | SKUs: {total_skus:,}", flush=True)
    return items


def get_shopify_inventory():
    url = f"https://{SHOPIFY_STORE_URL}/admin/api/2024-01/graphql.json"
    headers = {'X-Shopify-Access-Token': SHOPIFY_ACCESS_TOKEN, 'Content-Type': 'application/json'}
    inventory = {}
    cursor = None

    while True:
        query = """
        query getVariants($cursor: String) {
          productVariants(first: 250, after: $cursor) {
            pageInfo { hasNextPage endCursor }
            edges {
              node {
                sku
                inventoryQuantity
              }
            }
          }
        }
        """
        while True:
            try:
                r = requests.post(url, headers=headers, json={'query': query, 'variables': {'cursor': cursor}}, timeout=30)
                data = r.json()
                break
            except Exception as e:
                print(f"Shopify error: {e}, retrying...", flush=True)
                time.sleep(5)

        cost = data.get('extensions', {}).get('cost', {})
        if cost.get('throttleStatus', {}).get('currentlyAvailable', 1000) < 500:
            time.sleep(2)

        if 'data' not in data:
            break

        for edge in data['data']['productVariants']['edges']:
            node = edge['node']
            sku = (node.get('sku') or '').strip()
            qty = node.get('inventoryQuantity') or 0
            if sku:
                inventory[sku] = max(0, qty)

        page_info = data['data']['productVariants']['pageInfo']
        if not page_info.get('hasNextPage'):
            break
        cursor = page_info['endCursor']

    print(f"Shopify inventory loaded: {len(inventory):,} SKUs", flush=True)
    return inventory


def max_qty_for_sku(sku):
    """SET4-<truck name> SKUs (set-of-4 truck-specific listings) are capped at 1;
    everything else is capped at 4."""
    return 1 if 'SET4-' in sku else 4


def load_ended_state():
    """Listings this script has ended for hitting 0 in Shopify, keyed by account name.
    Persisted to disk (and committed back to the repo by the GitHub Actions workflow)
    so a later run can find them again — an ended listing no longer shows up in
    get_ebay_listings(), so this is the only record that it should be relisted once
    its SKU is back in stock.
    """
    if not os.path.exists(ENDED_STATE_PATH):
        return {}
    with open(ENDED_STATE_PATH) as f:
        return json.load(f)


def save_ended_state(state):
    os.makedirs(os.path.dirname(ENDED_STATE_PATH), exist_ok=True)
    with open(ENDED_STATE_PATH, 'w') as f:
        json.dump(state, f, indent=2, sort_keys=True)
        f.write('\n')


def relist_restocked_items(account_ended, inventory, user_token=EBAY_USER_TOKEN):
    """Relist single-SKU listings we previously ended, now that their SKU is back
    in stock in Shopify. Mutates account_ended in place, removing relisted entries."""
    to_relist = [
        (item_id, entry['sku']) for item_id, entry in account_ended.items()
        if inventory.get(entry['sku'], 0) > 0
    ]
    if not to_relist:
        return 0

    print(f"Relisting {len(to_relist):,} previously-ended listings back in stock...", flush=True)
    relisted = 0
    for item_id, sku in to_relist:
        qty = min(inventory[sku], max_qty_for_sku(sku))

        # RelistFixedPriceItem enforces that the SiteID header match the listing's
        # original site (unlike Revise/GetItem, which tolerate a mismatch) — e.g.
        # wheel/tire listings often live under eBayMotors (100), not the default
        # US site (0). Look it up per item rather than assuming.
        item_root = ebay_call('GetItem', f"<ItemID>{item_id}</ItemID>", user_token=user_token)
        site_name = item_root.findtext('.//e:Item/e:Site', 'US', NS)
        site_id = SITE_NAME_TO_ID.get(site_name, '0')

        root = ebay_call('RelistFixedPriceItem', f"""
  <Item>
    <ItemID>{item_id}</ItemID>
    <Quantity>{qty}</Quantity>
  </Item>
""", user_token=user_token, site_id=site_id)
        ack = root.findtext('e:Ack', '', NS)
        if ack in ('Success', 'Warning'):
            new_item_id = root.findtext('e:ItemID', '', NS)
            relisted += 1
            del account_ended[item_id]
            print(f"  Relisted (old ItemID {item_id} -> new ItemID {new_item_id}, SKU {sku}={qty}): back in stock", flush=True)
        else:
            errors = root.findall('.//e:Errors', NS)
            # ErrorCode 21919067: "you already have an identical listing active" —
            # something else (a manual relist, etc.) already put this SKU back on
            # eBay since we ended it. Nothing to do; stop tracking it.
            already_relisted = any(e.findtext('e:ErrorCode', '', NS) == '21919067' for e in errors)
            if already_relisted:
                del account_ended[item_id]
                print(f"  Skipped (ItemID {item_id}, SKU {sku}): already relisted elsewhere, dropping from tracked state", flush=True)
            else:
                for e in errors:
                    if e.findtext('e:SeverityCode', '', NS) == 'Error':
                        print(f"  Error relisting (ItemID {item_id}, SKU {sku}): {e.findtext('e:LongMessage', '', NS)}", flush=True)
        time.sleep(0.25)

    print(f"Relisting complete: {relisted:,} listings relisted.", flush=True)
    return relisted


def update_ebay_quantities(items, inventory, account_ended, user_token=EBAY_USER_TOKEN):
    matched = {}
    for item_id, info in items.items():
        var_list = [(sku, min(inventory[sku], max_qty_for_sku(sku))) for sku in info['skus'] if sku in inventory]
        if var_list:
            matched[item_id] = {'vars': var_list, 'is_variation': info['is_variation']}

    if not matched:
        print("No matching SKUs found between eBay and Shopify.", flush=True)
        return

    print(f"Syncing {len(matched):,} eBay listings...", flush=True)
    updated = 0
    ended = 0

    for item_id, m in matched.items():
        var_list = m['vars']
        skus_str = ', '.join(f"{sku}={qty}" for sku, qty in var_list)

        # Single-SKU (non-variation) listings can't be revised to Quantity=0 —
        # eBay rejects it unless "Out of stock" control is enabled on the account.
        # End the listing instead so it can't be oversold, and remember it so a
        # later run can relist it once Shopify has stock again.
        if not m['is_variation'] and var_list[0][1] == 0:
            root = ebay_call('EndFixedPriceItem', f"""
  <ItemID>{item_id}</ItemID>
  <EndingReason>NotAvailable</EndingReason>
""", user_token=user_token)
            if root.findtext('e:Ack', '', NS) in ('Success', 'Warning'):
                ended += 1
                account_ended[item_id] = {'sku': var_list[0][0]}
                print(f"  Ended (ItemID {item_id}, SKU {skus_str}): out of stock in Shopify", flush=True)
            else:
                for e in root.findall('.//e:Errors', NS):
                    if e.findtext('e:SeverityCode', '', NS) == 'Error':
                        print(f"  Error ending (ItemID {item_id}, SKU {skus_str}): {e.findtext('e:LongMessage', '', NS)}", flush=True)
            time.sleep(0.25)
            continue

        vars_xml = ''.join(
            f"<Variation><SKU>{sku}</SKU><Quantity>{qty}</Quantity></Variation>"
            for sku, qty in var_list
        )
        root = ebay_call('ReviseFixedPriceItem', f"""
  <Item>
    <ItemID>{item_id}</ItemID>
    <Variations>{vars_xml}</Variations>
  </Item>
""", user_token=user_token)
        ack = root.findtext('e:Ack', '', NS)
        if ack in ('Success', 'Warning'):
            updated += 1
        else:
            errors = root.findall('.//e:Errors', NS)
            not_multi = any('Multi-SKU' in (e.findtext('e:LongMessage', '', NS) or '') for e in errors)
            if not_multi and var_list:
                # Single-variation listing — retry without Variations wrapper
                qty = var_list[0][1]
                root2 = ebay_call('ReviseFixedPriceItem', f"""
  <Item><ItemID>{item_id}</ItemID><Quantity>{qty}</Quantity></Item>
""", user_token=user_token)
                if root2.findtext('e:Ack', '', NS) in ('Success', 'Warning'):
                    updated += 1
                else:
                    for e in root2.findall('.//e:Errors', NS):
                        if e.findtext('e:SeverityCode', '', NS) == 'Error':
                            print(f"  Error (ItemID {item_id}, SKU {skus_str}): {e.findtext('e:LongMessage', '', NS)}", flush=True)
            else:
                for e in errors:
                    msg = e.findtext('e:LongMessage', '', NS) or ''
                    if e.findtext('e:SeverityCode', '', NS) == 'Error' and 'ended' not in msg.lower():
                        print(f"  Error (ItemID {item_id}, SKU {skus_str}): {msg}", flush=True)
        time.sleep(0.25)

    print(f"eBay inventory sync complete: {updated:,} listings updated, {ended:,} ended (out of stock).", flush=True)


def main():
    accounts = [(name, token) for name, token in EBAY_ACCOUNTS if token]
    if not all([EBAY_APP_ID, EBAY_DEV_ID, EBAY_CERT_ID, SHOPIFY_STORE_URL, SHOPIFY_ACCESS_TOKEN]) or not accounts:
        print("ERROR: Missing credentials in environment.", flush=True)
        return

    print("=== Shopify -> eBay Inventory Sync ===", flush=True)
    shopify_inventory = get_shopify_inventory()
    if not shopify_inventory:
        print("No Shopify inventory found.", flush=True)
        return

    ended_state = load_ended_state()

    for name, token in accounts:
        print(f"--- eBay account: {name} ---", flush=True)
        account_ended = ended_state.setdefault(name, {})

        relist_restocked_items(account_ended, shopify_inventory, user_token=token)

        ebay_listings = get_ebay_listings(user_token=token)
        if not ebay_listings:
            print(f"No eBay listings found for {name}.", flush=True)
            continue
        update_ebay_quantities(ebay_listings, shopify_inventory, account_ended, user_token=token)

    save_ended_state(ended_state)
    print("All done.", flush=True)


if __name__ == "__main__":
    main()