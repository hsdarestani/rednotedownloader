#!/usr/bin/env python3
"""Make DirectAdmin mysqldump INSERTs explicit-column for Rishe's verified legacy importer.

All source data stays on VPS. Refuse missing schema; never print or upload row values.
"""
from collections import Counter
from pathlib import Path
import re,os

BASE=Path("/opt/rishestore-pars/legacy-20261020")
SOURCE=BASE/"rishesto_db.sql"
DEST=BASE/"rishesto_compatible.sql"
TABLES={
"wp_users","wp_usermeta","wp_wc_customer_lookup","wp_wc_orders",
"wp_wc_order_addresses","wp_wc_order_operational_data",
"wp_woocommerce_order_items","wp_woocommerce_order_itemmeta",
"wp_rishe_sales_events","wp_rishe_event_sales","wp_rishe_event_sale_lines"
}
s=SOURCE.read_text(encoding="utf-8",errors="replace")
columns={}
for m in re.finditer(r'(?im)^CREATE TABLE\s+(?:IF NOT EXISTS\s+)?`?(wp_[a-z0-9_]+)`?\s*\((.*?)^\)\s*(?:ENGINE|;)',s,re.S|re.M):
    tab=m.group(1)
    if tab not in TABLES: continue
    cols=re.findall(r'(?m)^\s*`([^`]+)`\s+',m.group(2))
    if not cols: raise RuntimeError("No SQL schema columns for "+tab)
    columns[tab]=cols
for mandatory in ("wp_users","wp_wc_orders","wp_woocommerce_order_items","wp_wc_order_addresses"):
    if mandatory not in columns: raise RuntimeError("Required schema table absent: "+mandatory)
counts=Counter()
pattern=re.compile(r'(?i)(INSERT\s+INTO\s+`?(wp_[a-z0-9_]+)`?)\s+(VALUES\s*)')
def add_columns(m):
    table=m.group(2)
    if table not in TABLES: return m.group(0)
    if table not in columns: raise RuntimeError("Missing schema for SQL INSERT: "+table)
    counts[table]+=1
    coltext=", ".join("`"+c+"`" for c in columns[table])
    return m.group(1)+" ("+coltext+") "+m.group(3)
transformed=pattern.sub(add_columns,s)
for mandatory in ("wp_users","wp_wc_orders","wp_woocommerce_order_items"):
    if counts[mandatory]<1: raise RuntimeError("Required SQL INSERT absent: "+mandatory)
tmp=DEST.with_suffix(".sql.partial")
with open(tmp,"w",encoding="utf-8") as o: o.write(transformed)
os.chmod(tmp,0o600)
os.replace(tmp,DEST)
print("SQL_COMPATIBLE_BYTES=",DEST.stat().st_size)
print("CONVERTED_INSERT_STATEMENTS=",dict(counts))
print("SCHEMA_COLUMN_COUNTS=",{x:len(columns[x]) for x in sorted(columns)})
