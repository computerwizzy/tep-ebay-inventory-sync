import os
import time
import datetime
import requests
import xml.etree.ElementTree as ET
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE_DIR, '.env'))

EBAY_APP_ID     = os.environ.get('EBAY_APP_ID')
EBAY_DEV_ID     = os.environ.get('EBAY_DEV_ID')
EBAY_CERT_ID    = os.environ.get('EBAY_CERT_ID')
EBAY_USER_TOKEN = os.environ.get('EBAY_USER_TOKEN')
EBAY_API_URL    = 'https://api.ebay.com/ws/api.dll'

SHOPIFY_STORE_URL    = os.environ.get('SHOPIFY_STORE_URL')
SHOPIFY_ACCESS_TOKEN = os.environ.get('SHOPIFY_ACCESS_TOKEN')

NS = {'e': 'urn:ebay:apis:eBLBaseComponents'}


def ebay_call(call_name, body_xml):
    headers = {
        'X-EBAY-API-SITEID': '0',
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
    <eBayAuthToken>{EBAY_USER_TOKEN}</eBayAuthToken>
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


def get_ebay_listings():
    """Return dict of item_id -> [sku, ...] for all active listings.
    Uses GetSellerList+Fine to get variation-level SKUs.
    GTC listings always end within 30 days, so EndTimeTo=now+32 captures all.
    """
    items = {}  # item_id -> [sku, ...]
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
""")
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
                items[item_id] = skus

        has_more = root.findtext('.//e:HasMoreItems', 'false', NS)
        if has_more.lower() != 'true':
            break
        page += 1
        time.sleep(0.5)

    total_skus = sum(len(v) for v in items.values())
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


def update_ebay_quantities(items, inventory):
    matched = {item_id: [(sku, min(inventory[sku], 4)) for sku in skus if sku in inventory]
               for item_id, skus in items.items()}
    matched = {k: v for k, v in matched.items() if v}

    if not matched:
        print("No matching SKUs found between eBay and Shopify.", flush=True)
        return

    print(f"Syncing {len(matched):,} eBay listings...", flush=True)
    updated = 0

    for item_id, var_list in matched.items():
        vars_xml = ''.join(
            f"<Variation><SKU>{sku}</SKU><Quantity>{qty}</Quantity></Variation>"
            for sku, qty in var_list
        )
        root = ebay_call('ReviseFixedPriceItem', f"""
  <Item>
    <ItemID>{item_id}</ItemID>
    <Variations>{vars_xml}</Variations>
  </Item>
""")
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
""")
                if root2.findtext('e:Ack', '', NS) in ('Success', 'Warning'):
                    updated += 1
                else:
                    for e in root2.findall('.//e:Errors', NS):
                        if e.findtext('e:SeverityCode', '', NS) == 'Error':
                            print(f"  Error (ItemID {item_id}): {e.findtext('e:LongMessage', '', NS)}", flush=True)
            else:
                for e in errors:
                    msg = e.findtext('e:LongMessage', '', NS) or ''
                    if e.findtext('e:SeverityCode', '', NS) == 'Error' and 'ended' not in msg.lower():
                        print(f"  Error (ItemID {item_id}): {msg}", flush=True)
        time.sleep(0.25)

    print(f"eBay inventory sync complete: {updated:,} listings updated.", flush=True)


def main():
    if not all([EBAY_APP_ID, EBAY_DEV_ID, EBAY_CERT_ID, EBAY_USER_TOKEN, SHOPIFY_STORE_URL, SHOPIFY_ACCESS_TOKEN]):
        print("ERROR: Missing credentials in environment.", flush=True)
        return

    print("=== Shopify -> eBay Inventory Sync ===", flush=True)
    ebay_listings = get_ebay_listings()
    if not ebay_listings:
        print("No eBay listings found.", flush=True)
        return

    shopify_inventory = get_shopify_inventory()
    if not shopify_inventory:
        print("No Shopify inventory found.", flush=True)
        return

    update_ebay_quantities(ebay_listings, shopify_inventory)
    print("All done.", flush=True)


if __name__ == "__main__":
    main()