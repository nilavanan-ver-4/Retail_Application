"""Drive the ORIGINAL Flask app (unchanged code) against a legacy-schema DB and record observable DB state."""
import sys, json, os, decimal, datetime
sys.path.insert(0, sys.argv[1])
os.chdir(sys.argv[1])
import app as legacy
legacy.db_config.update(dbname="legacy_retail", user="retail", password="retail_dev_pw", host="localhost")
legacy.app.config["TESTING"] = True
c = legacy.app.test_client()
q = legacy.fetch_data
out = {}
def post(url, data): return c.post(url, data=data, follow_redirects=False).status_code
out["add_supplier"] = post("/add_supplier", dict(name="ABC", company="ABC Pvt", contact_num="9876543210", email="a@x.com", terms="Net 30"))
out["add_product1"] = post("/add_product", dict(pro_id="1", pro_name="Apple", cost_price="50", selling_price="70", quantity="100", unit="kg", category="Fruits", safety_level="20", supplier_id="1", supplier_name="ABC", hc_code="H1", expiry_date="2026-12-31"))
out["add_product2"] = post("/add_product", dict(pro_id="2", pro_name="Milk", cost_price="30", selling_price="45", quantity="50", unit="l", category="Dairy", safety_level="10", supplier_id="1", supplier_name="ABC", hc_code="H2", expiry_date="2026-12-31"))
out["inventory_after_add"] = q("select product_id,quantity,safety_level,availability,price from inventory order by 1")
out["add_product_qty_after_add"] = q("select pro_id,quantity from add_product order by 1")
# Invoice 1: cash, fully paid, 2 lines w/ line discount, 10% invoice discount
form = {"date":"2025-01-10","terms":"Cash","sale_by":"Ali","mobile":"0123","name":"Cust A","address":"KL","mail":"a@a.com",
 "total_amount":"243.00","discount_percent":"10","discount":"24.30","final_amount":"218.70","payment_method":"cash","total_paid":"218.70"}
from werkzeug.datastructures import MultiDict
def mdict(base, items):
    m = MultiDict(base)
    for k in ("product_id[]","quantity[]","price[]","discount[]"):
        for it in items: m.add(k, it[k])
    return m
items = [{"product_id[]":"1","quantity[]":"3","price[]":"70","discount[]":"10"},{"product_id[]":"2","quantity[]":"1","price[]":"45","discount[]":"2"}]
# total = (3*70-10)+(45-2)=200+43=243
out["invoice1_post"] = c.post("/create_invoice", data=mdict(form, items)).status_code
# Invoice 2: unpaid
form2 = dict(form, mobile="0456", name="Cust B", payment_method="unpaid", total_paid="0", total_amount="140.00", discount_percent="0", discount="0", final_amount="140.00")
out["invoice2_post"] = c.post("/create_invoice", data=mdict(form2, [{"product_id[]":"1","quantity[]":"2","price[]":"70","discount[]":"0"}])).status_code
# Invoice 3: oversell attempt
form3 = dict(form2, mobile="0789", name="Cust C", total_amount="7000.00", final_amount="7000.00")
out["invoice3_oversell_post"] = c.post("/create_invoice", data=mdict(form3, [{"product_id[]":"1","quantity[]":"100","price[]":"70","discount[]":"0"}])).status_code
# tampered: client sends mismatched totals
form4 = dict(form, mobile="0999", name="Cust D", total_amount="10.00", discount_percent="0", discount="0", final_amount="10.00")
out["invoice4_tampered_post"] = c.post("/create_invoice", data=mdict(form4, [{"product_id[]":"1","quantity[]":"1","price[]":"70","discount[]":"0"}])).status_code
out["invoice4_final_stored"] = q("select invoice_no,total_amount,final_amount from invoice where customer_mobile='0999'")
# add_item (goods receipt) +25 apples
out["add_item_post"] = c.post("/add_item?product_id=1", data={"quantity_added":"25","comments":"restock"}).status_code
# manual ledger credit
out["add_ledger_post"] = c.post("/add_ledger", data={"transaction_date":"2025-01-11","identifier_type":"mobile","customer_id":"0456","amount":"50","transaction_type":"Credit","description":"manual"}).status_code
dump = lambda sql: [[ (float(v) if isinstance(v, decimal.Decimal) else (str(v) if isinstance(v,(datetime.date,datetime.datetime)) else v)) for v in r] for r in q(sql)]
out["invoice"] = dump("select invoice_no,customer_id,customer_name,date,total_amount,discount,final_amount,status,payment_status,total_paid,payment_method from invoice order by 1")
out["invoice_products"] = dump("select invoice_no,product_id,quantity,unit,price,discount,total from invoice_products order by id")
out["payment"] = dump("select invoice_no,amount,payment_mode,status,customer_id from payment order by id")
out["ledger"] = dump("select customer_mobile,amount,transaction_type,balance,left(description,40) from ledger order by id")
out["inventory"] = dump("select product_id,quantity,availability from inventory order by 1")
out["add_product_qty"] = dump("select pro_id,quantity from add_product order by 1")
out["supplier_invoice"] = dump("select invoice_no,supplier_id,product_id,quantity,total_amount,status,payment_status from supplier_invoice")
out["dashboard_status"] = c.get("/").status_code
for p in ["/products","/inventory","/customers","/suppliers","/invoices","/payments","/ledger","/filters","/order_history","/create_invoice"]:
    try: out["GET "+p] = c.get(p).status_code
    except Exception as e: out["GET "+p] = "EXC "+type(e).__name__
json.dump(out, open(sys.argv[2],"w"), indent=1, default=str)
print(json.dumps(out, indent=1, default=str))
