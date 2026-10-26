from flask import Flask, render_template, request, redirect, url_for ,jsonify , flash
import psycopg2
import logging
from datetime import datetime
import os
from werkzeug.utils import secure_filename
import pandas as pd

UPLOAD_FOLDER = 'uploads'  # Folder to store uploaded files
ALLOWED_EXTENSIONS = {'csv', 'xlsx'}



# Ensure upload folder exists
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

app = Flask(__name__, static_folder='static', static_url_path='/static')
app.secret_key = 'your_super_secret_key_here'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
# PostgreSQL connection parameters
db_config = {
    "dbname": "postgres",  
    "user": "postgres",    
    "password": "1234",  
    "host": "localhost",   
    "port": "5432"         
}

# Configure logging
logging.basicConfig(level=logging.INFO)

# Function to fetch data from PostgreSQL
def fetch_data(query, params=None):
    try:
        with psycopg2.connect(**db_config) as conn:
            with conn.cursor() as cursor:
                cursor.execute(query, params or ())
                return cursor.fetchall()
    except psycopg2.Error as e:
        logging.error("Database Error: %s", str(e))
        return []

# Function to execute queries for update/delete/insert and return fetched data if required
def execute_query(query, params=None, fetch=False):
    try:
        with psycopg2.connect(**db_config) as conn:
            with conn.cursor() as cursor:
                cursor.execute(query, params or ())
                if fetch:
                    return cursor.fetchall()  # Return result if 'fetch' is True
                conn.commit()  # Commit the transaction if not fetching
                return True
    except psycopg2.Error as e:
        logging.error("Database error: %s", str(e))
        return None





@app.route('/')
def dashboard():
    # Fetching dashboard data
    queries = {
        "total_customers": "SELECT COUNT(*) FROM customer",
        "total_sales_today": "SELECT COALESCE(SUM(final_amount), 0) FROM invoice WHERE date = CURRENT_DATE",
        "total_sales_month": "SELECT COALESCE(SUM(final_amount), 0) FROM invoice WHERE date >= DATE_TRUNC('month', CURRENT_DATE)",
        "total_sales_week": "SELECT COALESCE(SUM(final_amount), 0) FROM invoice WHERE date >= CURRENT_DATE - INTERVAL '7 days'",
        "total_orders": "SELECT COUNT(*) FROM invoice",
        "average_order_value": "SELECT COALESCE(AVG(final_amount), 0) FROM invoice",
        "active_customers": "SELECT COUNT(DISTINCT customer_id) FROM invoice WHERE date >= CURRENT_DATE - INTERVAL '30 days'",
        "best_selling_products": """
            SELECT p.pro_name, SUM(ip.quantity) AS total_sold
            FROM invoice_products ip
            JOIN add_product p ON ip.product_id = p.pro_id
            GROUP BY p.pro_name
            ORDER BY total_sold DESC LIMIT 5
        """,
        "top_customers": """
            SELECT c.name, SUM(i.final_amount) AS total_spent
            FROM invoice i
            JOIN customer c ON i.customer_id = c.id
            GROUP BY c.name
            ORDER BY total_spent DESC LIMIT 5
        """,
        "low_stock": "SELECT product_name, quantity FROM inventory WHERE quantity <= 10",
        "reorder_stock": "SELECT product_name, quantity FROM inventory WHERE quantity <= 5",
        "total_revenue": "SELECT COALESCE(SUM(amount), 0) FROM payment WHERE invoice_date >= DATE_TRUNC('month', CURRENT_DATE)",
        "pending_payments": """
            SELECT COUNT(*) FROM invoice i 
            LEFT JOIN payment p ON i.invoice_no = p.invoice_no
            WHERE p.status = 'Unpaid'
        """,
        "payment_mode_distribution": """
            SELECT payment_mode, COUNT(*), SUM(amount) FROM payment GROUP BY payment_mode
        """,
        "daily_sales_trend": """
            SELECT date, SUM(final_amount) 
            FROM invoice 
            WHERE date >= CURRENT_DATE - INTERVAL '7 days'
            GROUP BY date ORDER BY date
        """
    }

    dashboard_data = {key: fetch_data(query) for key, query in queries.items()}

    return render_template('dashboard.html', data=dashboard_data)













# Route to get customer details or create a new customer
@app.route('/get_customer', methods=['GET'])
def get_customer():
    try:
        mobile = request.args.get('mobile')
        name = request.args.get('name')

        if not mobile:
            return jsonify({"error": "Mobile number is required"}), 400

        customer_query = "SELECT id, name FROM customer WHERE mobile = %s"
        customer = fetch_data(customer_query, (mobile,))

        if customer:
            return jsonify({"id": customer[0][0], "name": customer[0][1]})
        else:
            if not name:
                return jsonify({"error": "Name is required to create a new customer"}), 400

            insert_query = "INSERT INTO customer (mobile, name) VALUES (%s, %s)"
            execute_query(insert_query, (mobile, name))
            return jsonify({"message": "Customer created successfully"}), 201

    except Exception as e:
        print("Error fetching customer:", e)
        return jsonify({"error": "Internal server error"}), 500

# Route to display the form for adding a new customer
@app.route('/add_customer', methods=['GET'])
def add_customer():
    return render_template('add_customer.html')

# Route to handle adding a new customer
@app.route('/add_customer', methods=['POST'])
def add_customer_post():
    try:
        mobile = request.form['mobile']
        name = request.form['name']
        address = request.form['address']
        mail = request.form['mail']

        if not mobile or not name:
            flash("Mobile and name are required.", 'error')
            return redirect(url_for('add_customer'))

        if not mobile.isdigit():
            flash("Mobile number should only contain digits.", 'error')
            return redirect(url_for('add_customer'))

        insert_query = "INSERT INTO customer (mobile, name, address, mail) VALUES (%s, %s, %s, %s)"
        execute_query(insert_query, (mobile, name, address, mail))

        flash("Customer added successfully!", 'success')
        return redirect(url_for('customers'))
    except Exception as e:
        flash(f"Error adding customer: {str(e)}", 'error')
        return redirect(url_for('add_customer'))

@app.route('/import_customers', methods=['GET', 'POST'])
def import_customers():
    if request.method == 'POST':
        if 'file' not in request.files:
            flash('No file selected!', 'danger')
            return redirect(url_for('customers'))

        file = request.files['file']

        if file.filename == '':
            flash('No file selected!', 'danger')
            return redirect(url_for('customers'))

        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)

            try:
                if filename.endswith('.csv'):
                    df = pd.read_csv(filepath)
                else:  # Excel file
                    df = pd.read_excel(filepath)

                required_columns = {'name', 'mobile', 'address', 'mail'}
                if not required_columns.issubset(df.columns):
                    flash("Invalid file format! Must contain 'name', 'mobile', 'address', 'mail'.", "danger")
                    return redirect(url_for('customers'))

                for _, row in df.iterrows():
                    name = row['name']
                    mobile = str(row['mobile']).strip()
                    address = row.get('address', '')
                    mail = row.get('mail', '')

                    # Check if mobile number already exists
                    check_query = "SELECT id FROM customer WHERE mobile = %s"
                    existing_customer = fetch_data(check_query, (mobile,))
                    if existing_customer:
                        continue  # Skip duplicates

                    # Insert into database
                    query = "INSERT INTO customer (mobile, name, address, mail) VALUES (%s, %s, %s, %s)"
                    execute_query(query, (mobile, name, address, mail))

                flash("Customers imported successfully!", "success")
            except Exception as e:
                flash(f"Error processing file: {str(e)}", "danger")

        else:
            flash("Invalid file format! Only CSV and Excel files are allowed.", "danger")

    return redirect(url_for('customers'))

# Route to display the update form for a customer
@app.route('/update_customer/<int:customer_id>', methods=['GET'])
def update_customer(customer_id):
    try:
        query = "SELECT * FROM customer WHERE id = %s"
        customer_data = fetch_data(query, (customer_id,))

        if customer_data:
            return render_template('update_customer.html', customer=customer_data[0])
        else:
            flash("Customer not found.", 'error')
            return redirect(url_for('customers'))
    except Exception as e:
        flash(f"Error fetching customer for update: {str(e)}", 'error')
        return redirect(url_for('customers'))

# Route to handle updating a customer
@app.route('/update_customer/<int:customer_id>', methods=['POST'])
def update_customer_post(customer_id):
    try:
        mobile = request.form['mobile']
        name = request.form['name']
        address = request.form['address']
        mail = request.form['mail']

        if not mobile or not name:
            flash("Mobile and name are required.", 'error')
            return redirect(url_for('update_customer', customer_id=customer_id))

        update_query = "UPDATE customer SET mobile = %s, name = %s, address = %s, mail = %s WHERE id = %s"
        execute_query(update_query, (mobile, name, address, mail, customer_id))

        flash("Customer updated successfully!", 'success')
        return redirect(url_for('customers'))
    except Exception as e:
        flash(f"Error updating customer: {str(e)}", 'error')
        return redirect(url_for('update_customer', customer_id=customer_id))

# Route to display and handle customer listing and addition
@app.route('/customers', methods=['GET', 'POST'])
def customers():
    if request.method == 'POST':
        try:
            name = request.form.get('name')
            mobile = request.form.get('mobile')
            address = request.form.get('address')
            mail = request.form.get('mail')

            if not name or not mobile:
                flash("Name and Mobile are required!", "danger")
                return redirect(url_for('customers'))

            query = "INSERT INTO customer (mobile, name, address, mail) VALUES (%s, %s, %s, %s)"
            execute_query(query, (mobile, name, address, mail))

            flash(f"Customer {name} added successfully!", "success")
            return redirect(url_for('customers'))
        except Exception as e:
            flash("Error adding customer!", "danger")
            return redirect(url_for('customers'))
    
    query = "SELECT * FROM customer"
    customers_data = fetch_data(query)
    return render_template('customers.html', data=customers_data)














@app.route('/suppliers', methods=['GET', 'POST'])
def suppliers():
    if request.method == 'POST':
        try:
            # Fetch form data
            name = request.form.get('name')
            company = request.form.get('company')
            contact_num = request.form.get('contact_num')  # Use contact_num for mobile
            email = request.form.get('email')  # Use email for mail

            # Check if required fields are empty
            if not name or not company or not contact_num or not email:
                flash("All fields are required!", "danger")
                return redirect(url_for('suppliers'))

            # Insert new supplier into the database
            query = """
                INSERT INTO supplier (name, company, mobile, mail) 
                VALUES (%s, %s, %s, %s)
            """

            execute_query(query, (name, company, contact_num, email))

            flash(f"Supplier {name} added successfully!", "success")
            return redirect(url_for('suppliers'))

        except Exception as e:
            logging.error(f"Error adding supplier: {str(e)}")
            flash("Error adding supplier!", "danger")
            return redirect(url_for('suppliers'))

    # Fetch and display existing suppliers
    query = "SELECT * FROM supplier"
    suppliers_data = fetch_data(query)
    return render_template('suppliers.html', data=suppliers_data)

# Route to Import Suppliers from CSV/Excel
@app.route('/import_suppliers', methods=['POST'])
def import_suppliers():
    if 'file' not in request.files:
        flash("No file selected!", "danger")
        return redirect(url_for('suppliers'))

    file = request.files['file']
    
    if file.filename == '':
        flash("No selected file!", "danger")
        return redirect(url_for('suppliers'))

    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)

        try:
            if filename.endswith('.csv'):
                df = pd.read_csv(filepath)
            else:
                df = pd.read_excel(filepath)

            for _, row in df.iterrows():
                execute_query("""
                    INSERT INTO supplier (name, company, mobile, mail, terms) 
                    VALUES (%s, %s, %s, %s, %s)
                """, (row['name'], row['company'], row['mobile'], row['mail'], row['terms']))

            flash("Suppliers imported successfully!", "success")
        except Exception as e:
            logging.error(f"Error importing suppliers: {str(e)}")
            flash("Error importing suppliers!", "danger")

    return redirect(url_for('suppliers'))



@app.route('/add_supplier', methods=['GET', 'POST'])
def add_supplier():
    if request.method == 'POST':
        try:
            # Fetch form data
            name = request.form.get('name')
            company = request.form.get('company')
            contact_num = request.form.get('contact_num')
            email = request.form.get('email')
            terms = request.form.get('terms')  # Fetch terms data

            # Check if required fields are empty
            if not name or not company or not contact_num or not email or not terms:
                flash("All fields are required!", "danger")
                return redirect(url_for('add_supplier'))

            # Insert new supplier into the database
            query = """
                INSERT INTO supplier (name, company, mobile, mail, terms) 
                VALUES (%s, %s, %s, %s, %s)
            """
            # Debugging log to verify data before insertion
            logging.info(f"Inserting supplier with: {name}, {company}, {contact_num}, {email}, {terms}")

            # Call the execute_query function to insert the data into the database
            execute_query(query, (name, company, contact_num, email, terms))

            flash(f"Supplier {name} added successfully!", "success")
            return redirect(url_for('suppliers'))

        except Exception as e:
            logging.error(f"Error adding supplier: {str(e)}")
            flash("Error adding supplier!", "danger")
            return redirect(url_for('add_supplier'))

    return render_template('add_supplier.html')  # Render the "Add Supplier" form

@app.route('/get_supplier_name/<int:supplier_id>', methods=['GET'])
def get_supplier_name(supplier_id):
    try:
        # Get supplier name by supplier_id
        supplier_name = get_supplier_name_by_id(supplier_id)
        if supplier_name:
            return jsonify({"supplier_name": supplier_name})
        else:
            return jsonify({"supplier_name": ""})  # If no supplier found
    except Exception as e:
        return jsonify({"supplier_name": ""})  # Handle errors gracefully

@app.route('/edit_supplier/<int:supplier_id>', methods=['GET', 'POST'])
def edit_supplier(supplier_id):
    try:
        # Fetch the supplier by ID
        supplier = get_supplier_by_id(supplier_id)  # Assuming you have a function to fetch the supplier by ID
        if not supplier:
            flash("Supplier not found!", "danger")
            return redirect(url_for('suppliers'))

        if request.method == 'POST':
            # Fetch form data to update the supplier
            name = request.form.get('name')
            company = request.form.get('company')
            contact_num = request.form.get('contact_num')
            email = request.form.get('email')

            # Validate required fields
            if not name or not company or not contact_num or not email:
                flash("All fields are required!", "danger")
                return redirect(url_for('edit_supplier', supplier_id=supplier_id))

            # Update supplier in the database
            query = """
                UPDATE supplier
                SET name = %s, company = %s, mobile = %s, mail = %s
                WHERE id = %s
            """
            execute_query(query, (name, company, contact_num, email, supplier_id))

            flash(f"Supplier {name} updated successfully!", "success")
            return redirect(url_for('suppliers'))

        # Render edit form with existing data
        return render_template('edit_supplier.html', supplier=supplier)

    except Exception as e:
        logging.error(f"Error editing supplier {supplier_id}: {str(e)}")
        flash("Error editing supplier!", "danger")
        return redirect(url_for('suppliers'))


@app.route('/get_suppliers')
def get_suppliers():
    try:
        query = "SELECT id, name FROM supplier ORDER BY name ASC"
        suppliers = execute_query(query, fetch=True)  # ✅ Ensure fetching data

        if not suppliers:
            print("DEBUG: No suppliers found in database!")
            return jsonify([])  # Return an empty list instead of an error
        
        # Swap ID and Name in Response
        supplier_list = [{'supplier_id': row[0], 'name': row[1]} for row in suppliers]
        print("DEBUG: Suppliers fetched:", supplier_list)

        return jsonify({'suppliers': supplier_list})  # Return as dictionary for better structure
    except Exception as e:
        print("DEBUG: Error fetching suppliers:", str(e))
        return jsonify({'error': str(e)})


""" def get_supplier_by_name(name):
    query = "SELECT id FROM supplier WHERE name = %s LIMIT 1"
    
    try:
        result = execute_query(query, params=(name,), fetch=True)  # Ensure fetch=True
        
        if result and len(result) > 0:  # Check if result is not empty
            return {'id': result[0][0]}  # Return the first match
        
        return None  # Return None if no supplier found

    except Exception as e:
        print(f"Error fetching supplier by name: {e}")
        return None """


def get_supplier_by_id(supplier_id):
    query = "SELECT id, name, company, mobile, mail FROM supplier WHERE id = %s"
    result = execute_query(query, (supplier_id,), fetch=True)  # Ensure fetch=True
    
    if result:
        # Convert tuple to dictionary
        return {
            'supplier_id': result[0][0],
            'name': result[0][1],
            'company': result[0][2],
            'mobile': result[0][3],
            'mail': result[0][4]
        }
    return None






@app.route('/add_product', methods=['GET', 'POST'])
def add_product():
    if request.method == 'POST':
        try:
            # Fetch form data
            pro_id = request.form.get("pro_id", "").strip()
            pro_name = request.form.get('pro_name', '').strip()
            cost_price = request.form.get('cost_price', "").strip()
            selling_price = request.form.get('selling_price', "").strip()
            quantity = request.form.get('quantity', "").strip()
            unit = request.form.get('unit', '').strip()
            category = request.form.get('category', '').strip()
            safety_level = request.form.get('safety_level', '0')  
            safety_level = int(safety_level)

            expiry_date = request.form.get('expiry_date', '').strip()
            supplier_id = request.form.get('supplier_id', '').strip()
            supplier_name = request.form.get('supplier_name', 'Unknown').strip()  # Changed here
            terms_from_invoice = request.form.get('terms_from_invoice', '').strip()
            hc_code = request.form.get('hc_code', '').strip()

            # Validate and convert numerical values
            try:
                cost_price = float(cost_price)
                selling_price = float(selling_price)
                quantity = int(quantity)
            except (ValueError, TypeError):
                flash("Invalid input format for numerical fields!", "danger")
                return redirect(url_for("add_product"))

            if not pro_id or not pro_name or cost_price <= 0 or selling_price <= 0 or quantity <= 0:
                flash("Missing or invalid required fields!", "danger")
                return redirect(url_for('add_product'))

            # Convert supplier_id if not empty
            try:
                supplier_id = int(supplier_id) if supplier_id else None
            except ValueError:
                flash("Invalid Supplier ID format!", "danger")
                return redirect(url_for("add_product"))

            # Fetch supplier details if supplier_id is missing but name exists
            if not supplier_id and supplier_name and supplier_name != "Unknown":
                supplier = get_supplier_by_name(supplier_name)
                supplier_id = supplier.get('supplier_id') if supplier else None

            if supplier_id is None:
                flash("Invalid supplier! Please check the supplier details.", "danger")
                return redirect(url_for('add_product'))

            # Insert into the database
            query = """
            INSERT INTO add_product 
            (pro_id, pro_name, cost_price, selling_price, quantity, unit, category, 
            safety_level, expiry_date, supplier_id, supplier_name, terms_from_invoice, hc_code)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """
            execute_query(query, (pro_id, pro_name, cost_price, selling_price, quantity, unit, 
                                  category, safety_level, expiry_date, supplier_id, supplier_name, 
                                  terms_from_invoice, hc_code))

            flash("Product added successfully!", "success")
            return redirect(url_for('inventory'))

        except Exception as e:
            flash(f"Error adding product: {e}", "danger")
            return redirect(url_for('add_product'))

    return render_template('add_product.html')


def get_supplier_by_name(supplier_name):
    """
    Fetch supplier details by name.
    Returns a dictionary {'supplier_id': int} if found, else None.
    """
    query = "SELECT id FROM supplier WHERE supplier_name = %s"
    result = execute_query(query, (supplier_name,))

    if result and len(result) > 0:
        return {"supplier_id": result[0][0]}
    return None




@app.route('/products', methods=['GET', 'POST'])
def products():
    if request.method == 'POST':
        try:
            # Fetch form data
            pro_name = request.form.get('pro_name')
            cost_price = request.form.get('cost_price')
            selling_price = request.form.get('selling_price')
            quantity = request.form.get('quantity')
            unit = request.form.get('unit')
            category = request.form.get('category')
            safety_level = request.form.get('safety_level', '0')  # Get value as string
            safety_level = int(safety_level)  # Convert to integer

            expiry_date = request.form.get('expiry_date', None)
            supplier_id = request.form.get('supplier_id', None)
            terms_from_invoice = request.form.get('terms_from_invoice', '')
            hc_code = request.form.get('hc_code', '')

            # Ensure required fields are not empty
            if not pro_name or not cost_price or not selling_price or not quantity:
                flash("Product Name, Cost Price, Selling Price, and Quantity are required!", "danger")
                return redirect(url_for('products'))

            # Insert the new product into the database
            query = """
                INSERT INTO add_product 
                (pro_name, cost_price, selling_price, quantity, unit, category, 
                safety_level, expiry_date, supplier_id, terms_from_invoice, hc_code)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """
            execute_query(query, (pro_name, cost_price, selling_price, quantity, unit, 
                                  category, safety_level, expiry_date, supplier_id, terms_from_invoice, hc_code))

            flash(f"Product {pro_name} added successfully!", "success")
            return redirect(url_for('products'))

        except Exception as e:
            logging.error(f"Error adding product: {str(e)}")
            flash("Error adding product!", "danger")
            return redirect(url_for('products'))

    # Fetch and display existing products
    query = "SELECT * FROM add_product"
    products_data = fetch_data(query)
    
    return render_template('products.html', data=products_data)
    
@app.route('/import_products', methods=['POST'])
def import_products():
    try:
        if 'file' not in request.files:
            flash("No file uploaded!", "danger")
            return redirect(url_for('products'))

        file = request.files['file']
        filename = file.filename.lower()

        if not filename:
            flash("No file selected!", "danger")
            return redirect(url_for('products'))

        # Read CSV or Excel file
        if filename.endswith('.csv'):
            df = pd.read_csv(file)
        elif filename.endswith('.xlsx'):
            df = pd.read_excel(file)
        else:
            flash("Unsupported file format! Please upload CSV or Excel.", "danger")
            return redirect(url_for('products'))

        print(f"DEBUG: Loaded DataFrame - \n{df.head()}")

        for _, row in df.iterrows():
            try:
                # Convert missing values
                row['expiry_date'] = None if pd.isna(row['expiry_date']) else row['expiry_date']
                row['safety_level'] = int(row['safety_level']) if pd.notna(row['safety_level']) else 0
                row['cost_price'] = float(row['cost_price'])
                row['selling_price'] = float(row['selling_price'])
                row['quantity'] = int(row['quantity'])
                row['supplier_id'] = int(row['supplier_id']) if pd.notna(row['supplier_id']) else None
                row['hc_code'] = None if pd.isna(row['hc_code']) else row['hc_code']

                # Fetch `supplier_name` from `supplier` table using `supplier_id`
                if row['supplier_id']:
                    supplier_query = "SELECT name FROM supplier WHERE id = %s"
                    supplier_data = fetch_data(supplier_query, (row['supplier_id'],))
                    row['supplier_name'] = supplier_data[0][0] if supplier_data else None

                # Ensure a valid supplier exists
                if row['supplier_id'] is None or row['supplier_name'] is None:
                    print(f"WARNING: Skipping product '{row['pro_name']}' due to missing supplier.")
                    continue  # Skip products with invalid suppliers

                # Insert product details into `add_product`
                product_query = """
                    INSERT INTO add_product 
                    (pro_id, pro_name, cost_price, selling_price, quantity, unit, category, 
                    safety_level, expiry_date, supplier_id, supplier_name, terms_from_invoice, hc_code)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (pro_id) DO UPDATE 
                    SET quantity = add_product.quantity + EXCLUDED.quantity
                """
                execute_query(product_query, (
                    row['pro_id'], row['pro_name'], row['cost_price'], row['selling_price'],
                    row['quantity'], row['unit'], row['category'], row['safety_level'],
                    row['expiry_date'], row['supplier_id'], row['supplier_name'],
                    row['terms_from_invoice'], row['hc_code']
                ))

                print(f"DEBUG: Added/Checked product in add_product: {row['pro_name']}")

                # Check if product exists in inventory
                check_query = "SELECT quantity FROM inventory WHERE product_id = %s"
                existing_product = fetch_data(check_query, (row['pro_id'],))

                if existing_product:
                    new_quantity = existing_product[0][0] + row['quantity']
                    update_query = "UPDATE inventory SET quantity = %s WHERE product_id = %s"
                    execute_query(update_query, (new_quantity, row['pro_id']))
                    print(f"DEBUG: Updated inventory for {row['pro_name']} (New quantity: {new_quantity})")
                else:
                    # Insert into inventory
                    insert_query = """
                        INSERT INTO inventory (product_id, product_name, price, quantity, unit, category, expiry_date)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """
                    execute_query(insert_query, (
                        row['pro_id'], row['pro_name'], row['selling_price'], row['quantity'],
                        row['unit'], row['category'], row['expiry_date']
                    ))
                    print(f"DEBUG: Inserted into inventory: {row['pro_name']}")

            except Exception as e:
                print(f"ERROR: Failed to insert row {row.to_dict()} - {e}")
                flash(f"Error inserting product: {row['pro_name']}", "danger")
                continue  # Skip this row and move to the next

        flash("Products imported and inventory updated successfully!", "success")
        return redirect(url_for('products'))

    except Exception as e:
        print(f"ERROR: Exception in import_products - {e}")
        flash("Error importing products!", "danger")
        return redirect(url_for('products'))






@app.route('/delete_product/<int:product_id>', methods=['POST'])
def delete_product(product_id):

    try:
        query = "DELETE FROM add_product WHERE pro_id = %s"
        execute_query(query, (product_id,))
        flash("Product deleted successfully!", "success")
    except Exception as e:
        logging.error(f"Error deleting product: {str(e)}")
        flash("Error deleting product!", "danger")

    return redirect(url_for('products'))

@app.route('/update_product/<int:product_id>', methods=['GET', 'POST'])
def update_product(product_id):
    if request.method == 'POST':
        try:
            pro_name = request.form.get('pro_name')
            cost_price = request.form.get('cost_price')
            selling_price = request.form.get('selling_price')
            quantity = request.form.get('quantity')
            unit = request.form.get('unit')
            category = request.form.get('category')
            safety_level = request.form.get('safety_level', '0')  
            safety_level = int(safety_level)

            expiry_date = request.form.get('expiry_date', None)
            supplier_id = request.form.get('supplier_id', None)
            supplier_name = request.form.get('supplier_name', '')  # Updated here

            # Validate required fields
            if not pro_name or not cost_price or not selling_price or not quantity:
                flash("Product Name, Cost Price, Selling Price, and Quantity are required!", "danger")
                return redirect(url_for('update_product', product_id=product_id))

            # Update product in database
            query = """
                UPDATE add_product 
                SET pro_name=%s, cost_price=%s, selling_price=%s, quantity=%s, unit=%s, 
                    category=%s, safety_level=%s, expiry_date=%s, supplier_id=%s, supplier_name=%s
                WHERE pro_id=%s
            """
            execute_query(query, (pro_name, cost_price, selling_price, quantity, unit, 
                                  category, safety_level, expiry_date, supplier_id, supplier_name, product_id))

            flash("Product updated successfully!", "success")
            return redirect(url_for('products'))

        except Exception as e:
            flash("Error updating product!", "danger")
            return redirect(url_for('update_product', product_id=product_id))

    # Fetch existing product data for display
    query = "SELECT * FROM add_product WHERE pro_id = %s"
    product_data = fetch_data(query, (product_id,))
    
    if not product_data:
        flash("Product not found!", "danger")
        return redirect(url_for('products'))

    return render_template('update_product.html', product=product_data[0])




@app.route('/invoices')
def invoices():
    query = """
    SELECT 
        i.invoice_no, i.customer_name, i.customer_mobile, i.date, i.terms, i.sale_by, 
        i.total_amount, i.discount, i.final_amount, i.status, i.payment_status
    FROM invoice i
    """
    raw_data = fetch_data(query)

    invoices_data = [
        {
            "invoice_no": row[0],
            "customer_name": row[1],
            "customer_mobile": row[2],
            "date": row[3],
            "terms": row[4],
            "sale_by": row[5],
            "total_amount": row[6],
            "discount": row[7],
            "final_amount": row[8],
            "status": row[9],
            "payment_status": row[10],
        }
        for row in raw_data
    ]

    print("Processed Data:", invoices_data)  # Debugging
    return render_template('invoices.html', invoices_data=invoices_data)

# @app.route('/create_invoice', methods=['GET', 'POST'])
# def create_invoice():
#     if request.method == 'GET':
#         # Fetch available products from inventory
#         product_query = "SELECT product_id, product_name, price FROM inventory WHERE availability = TRUE"
#         products = fetch_data(product_query)
#         return render_template('create_invoice.html', products=products)

#     elif request.method == 'POST':
#         try:
#             # Collect form data
#             invoice_no = request.form.get('invoice_no')
#             date = request.form.get('date')
#             terms = request.form.get('terms')
#             sales_by = request.form.get('sale_by')
#             customer_mobile = request.form.get('mobile')
#             customer_name = request.form.get('name')
#             customer_address = request.form.get('address', None)
#             customer_mail = request.form.get('mail', None)
#             discount = float(request.form.get('discount', 0))
#             status = request.form.get('status', 'Pending')
#             payment_method = request.form.get('payment_method', 'unpaid')  # Ensure this is correctly passed
#             total_paid = float(request.form.get('total_paid', 0))
            
#             # Log the payment details to ensure they are being received correctly
#             print(f"Payment Method: {payment_method}, Total Paid: {total_paid}")

#             # Corrected payment status logic
#             if payment_method in ['cash', 'credit_card', 'bank_transfer'] and total_paid > 0:
#                 payment_status = 'Paid'
#                 status = 'Completed'
#             else:
#                 payment_status = 'Unpaid'
#                 status = 'Pending'
#                 total_paid = 0.0  # Reset total_paid if unpaid

            
#             # Collect product-related data
#             product_ids = request.form.getlist('product_id[]')
#             quantities = list(map(int, request.form.getlist('quantity[]')))
#             prices = list(map(float, request.form.getlist('price[]')))
#             product_discounts = list(map(float, request.form.getlist('discount[]')))
            
#             # Ensure required fields are provided
#             if not all([date, terms, sales_by, customer_mobile, customer_name]) or not product_ids:
#                 flash("All required fields must be filled, and at least one product must be selected!", "warning")
#                 return redirect(url_for('create_invoice'))
            
#             # Handle customer lookup or creation
#             customer_query = "SELECT id, name, address, mail FROM customer WHERE mobile = %s"
#             customer_data = fetch_data(customer_query, (customer_mobile,))
#             if customer_data:
#                 customer_id, customer_name, customer_address, customer_mail = customer_data[0]
#             else:
#                 insert_customer_query = """
#                 INSERT INTO customer (mobile, name, address, mail) 
#                 VALUES (%s, %s, %s, %s) RETURNING id
#                 """
#                 customer_id = fetch_data(insert_customer_query, (customer_mobile, customer_name, customer_address, customer_mail))[0][0]
#                 flash(f"New customer created with ID: {customer_id}", "success")
            
#             # Process products and calculate totals
#             total_amount = 0
#             for i in range(len(product_ids)):
#                 if product_ids[i].lower() == "others":
#                     custom_name = request.form.get(f'custom_product_name[{i}]')
#                     custom_price = float(request.form.get(f'custom_product_price[{i}]'))
#                     custom_qty = int(request.form.get(f'custom_product_quantity[{i}]'))
#                     if not custom_name or custom_price <= 0 or custom_qty <= 0:
#                         flash(f"Provide valid details for the custom product at row {i+1}!", "warning")
#                         return redirect(url_for('create_invoice'))
                    
#                     insert_product_query = """
#                     INSERT INTO inventory (name, price, quantity, availability) 
#                     VALUES (%s, %s, %s, TRUE) RETURNING product_id
#                     """
#                     product_ids[i] = fetch_data(insert_product_query, (custom_name, custom_price, custom_qty))[0][0]
                
#                 total_amount += (prices[i] * quantities[i]) - product_discounts[i]
            
#             final_amount = total_amount - discount

#             # Calculate payment_status and status
#             if payment_method in ['cash', 'credit_card', 'bank_transfer'] and total_paid > 0:
#                 payment_status = 'Paid'
#                 status = 'Completed'
#             else:
#                 payment_status = 'Unpaid'
#                 status = 'Pending'
#                 total_paid = 0.0  # Reset total_paid if unpaid

#             # Now the invoice insertion query with the updated payment status and total paid
#             invoice_query = """
#             INSERT INTO invoice (date, terms, sale_by, customer_id, customer_name, customer_mobile, total_amount, discount, payment_status, status, total_paid) 
#             VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING invoice_no
#             """
#             invoice_id = execute_query(invoice_query, (date, terms, sales_by, customer_id, customer_name, customer_mobile, final_amount, discount, payment_status, status, total_paid), fetch=True)[0][0]

#             # Process invoice products
#             for i in range(len(product_ids)):
#                 qty, price, product_discount = quantities[i], prices[i], product_discounts[i]
                
#                 invoice_product_query = """
#                 INSERT INTO invoice_products (invoice_no, product_id, quantity, unit, price, discount) 
#                 VALUES (%s, %s, %s, (SELECT unit FROM inventory WHERE product_id = %s), %s, %s)
#                 """
#                 execute_query(invoice_product_query, (invoice_id, product_ids[i], qty, product_ids[i], price, product_discount))
                
#                 # Check stock availability
#                 stock_qty = fetch_data("SELECT quantity FROM inventory WHERE product_id = %s", (product_ids[i],))[0][0]
#                 if stock_qty < qty:
#                     flash(f"Not enough stock for product ID {product_ids[i]}", "danger")
#                     return redirect(url_for('create_invoice'))
                
#                 # Insert sale record
#                 sale_query = """
#                 INSERT INTO sale_product (invoice_no, product_id, quantity, price, discount) 
#                 VALUES (%s, %s, %s, %s, %s)
#                 """
#                 execute_query(sale_query, (invoice_id, product_ids[i], qty, price, product_discount))
                
#                 # Update inventory stock
#                 execute_query("UPDATE inventory SET quantity = quantity - %s WHERE product_id = %s", (qty, product_ids[i]))
            
#             flash(f"Invoice {invoice_id} created successfully!", "success")
#             return redirect(url_for('invoices'))
        
#         except Exception as e:
#             logging.error("Error creating invoice: %s", str(e))
#             flash("Error creating invoice!", "danger")
#             return redirect(url_for('create_invoice'))
@app.route('/create_invoice', methods=['GET', 'POST'])
def create_invoice():
    if request.method == 'GET':
        product_query = "SELECT product_id, product_name, price FROM inventory WHERE availability >= 3"
        products = fetch_data(product_query)
        return render_template('create_invoice_1.html', products=products)

    elif request.method == 'POST':
        try:
            # Collect form data
            date = request.form.get('date')
            terms = request.form.get('terms')
            sales_by = request.form.get('sale_by')
            customer_mobile = request.form.get('mobile')
            customer_name = request.form.get('name')
            customer_address = request.form.get('address', None)
            customer_mail = request.form.get('mail', None)
            total_amount = float(request.form.get('total_amount', 0))  # Before discount
            discount_percent = float(request.form.get('discount_percent', 0))  # Percentage
            discount = float(request.form.get('discount', 0))  # Calculated amount
            final_amount = float(request.form.get('final_amount', 0))  # After discount
            payment_method = request.form.get('payment_method', 'unpaid')
            total_paid = float(request.form.get('total_paid', 0))

            # Debug prints
            print(f"Raw Form Data: {request.form}")
            print(f"Payment Method: {payment_method}, Total Paid: {total_paid}, Total Amount: {total_amount}, Discount: {discount}")

            # Validate required fields
            if not all([date, terms, sales_by, customer_mobile, customer_name]) or not request.form.getlist('product_id[]'):
                flash("All required fields must be filled, and at least one product must be selected!", "warning")
                return redirect(url_for('create_invoice'))

            # Verify discount calculation
            calculated_discount = total_amount * (discount_percent / 100)
            if abs(calculated_discount - discount) > 0.01:
                flash("Discount amount does not match the calculated percentage!", "warning")
                return redirect(url_for('create_invoice'))
            if abs(total_amount - discount - final_amount) > 0.01:
                flash("Final Amount does not match Total Amount minus Discount!", "warning")
                return redirect(url_for('create_invoice'))

            # Payment validation
            paid_methods = ['cash', 'credit_card', 'bank_transfer', 'upi']
            if payment_method in paid_methods:
                if final_amount > 0 and total_paid <= 0:
                    flash("Total Paid must be greater than 0 when Final Amount is positive for paid methods!", "warning")
                    return redirect(url_for('create_invoice'))
            elif payment_method == 'unpaid':
                if total_paid != 0:
                    total_paid = 0.0
                    print("Total Paid forced to 0 for 'unpaid' method")

            print(f"Validated: Payment Method: {payment_method}, Total Paid: {total_paid}, Total Amount: {total_amount}, Discount: {discount}, Final Amount: {final_amount}")

            # Handle customer
            customer_query = "SELECT id FROM customer WHERE mobile = %s"
            customer_data = fetch_data(customer_query, (customer_mobile,))
            if customer_data:
                customer_id = customer_data[0][0]
            else:
                insert_customer_query = """
                INSERT INTO customer (mobile, name, address, mail) 
                VALUES (%s, %s, %s, %s) RETURNING id
                """
                customer_id = fetch_data(insert_customer_query, (customer_mobile, customer_name, customer_address, customer_mail))[0][0]
                flash(f"New customer created with ID: {customer_id}", "success")

            # Process products
            product_ids = request.form.getlist('product_id[]')
            quantities = list(map(int, request.form.getlist('quantity[]')))
            prices = list(map(float, request.form.getlist('price[]')))
            product_discounts = list(map(float, request.form.getlist('discount[]')))

            # Verify stock availability
            for i, product_id in enumerate(product_ids):
                qty = quantities[i]
                stock_query = "SELECT quantity FROM inventory WHERE product_id = %s"
                stock_data = fetch_data(stock_query, (product_id,))
                if not stock_data or stock_data[0][0] < qty:
                    flash(f"Not enough stock for product ID {product_id} (Requested: {qty}, Available: {stock_data[0][0] if stock_data else 0})", "danger")
                    return redirect(url_for('create_invoice'))

            # Insert invoice
            invoice_query = """
            INSERT INTO invoice (date, terms, sale_by, customer_id, customer_name, customer_mobile, total_amount, discount,  payment_method, total_paid)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING invoice_no
            """
            invoice_id = execute_query(invoice_query, (date, terms, sales_by, customer_id, customer_name, customer_mobile, total_amount, discount,  payment_method, total_paid), fetch=True)[0][0]

            # Process invoice products and update stock
            for i, product_id in enumerate(product_ids):
                qty, price, prod_discount = quantities[i], prices[i], product_discounts[i]
                invoice_product_query = """
                INSERT INTO invoice_products (invoice_no, product_id, quantity, unit, price, discount)
                VALUES (%s, %s, %s, (SELECT unit FROM inventory WHERE product_id = %s), %s, %s)
                """
                execute_query(invoice_product_query, (invoice_id, product_id, qty, product_id, price, prod_discount))
                execute_query("UPDATE inventory SET quantity = quantity - %s WHERE product_id = %s", (qty, product_id))

            flash(f"Invoice {invoice_id} created successfully!", "success")
            return redirect(url_for('invoices'))

        except ValueError as ve:
            logging.error("ValueError creating invoice: %s", str(ve))
            flash("Invalid numeric input provided!", "danger")
            return redirect(url_for('create_invoice'))
        except Exception as e:
            logging.error("Error creating invoice: %s", str(e))
            flash("Error creating invoice!", "danger")
            return redirect(url_for('create_invoice'))

@app.route('/get_invoice_details')
def get_invoice_details():
    invoice_no = request.args.get('invoice_no')

    # Validate invoice_no
    if not invoice_no:
        return jsonify({"error": "Invoice number is required"}), 400

    try:
        invoice_no = int(invoice_no)  # Ensure it's an integer
    except ValueError:
        return jsonify({"error": "Invalid invoice number format"}), 400

    # Query to fetch invoice details
    query = """
    SELECT 
        i.invoice_no, i.customer_name, i.customer_mobile, i.date, 
        i.final_amount, i.status, i.payment_status
    FROM invoice i
    WHERE i.invoice_no = %s
    """
    invoice = fetch_data(query, (invoice_no,))

    if not invoice:
        return jsonify({"error": "Invoice not found"}), 404

    # Query to fetch products with product name
    product_query = """
    SELECT p.pro_name, ip.quantity, ip.unit, ip.price, ip.discount, ip.total
    FROM invoice_products ip
    JOIN add_product p ON ip.product_id = p.pro_id
    WHERE ip.invoice_no = %s
    """
    products = fetch_data(product_query, (invoice_no,))

    # Debugging prints
    print(f"Invoice No: {invoice_no}")
    print(f"Fetched Invoice: {invoice}")
    print(f"Fetched Products: {products}")

    # If no products found, return invoice without items
    if not products:
        return jsonify({
            "invoice_no": invoice[0][0],
            "customer_name": invoice[0][1],
            "customer_mobile": invoice[0][2],
            "date": invoice[0][3],
            "final_amount": float(invoice[0][4]),  # Ensure float conversion
            "status": invoice[0][5],
            "payment_status": invoice[0][6],
            "items": []
        })

    return jsonify({
        "invoice_no": invoice[0][0],
        "customer_name": invoice[0][1],
        "customer_mobile": invoice[0][2],
        "date": invoice[0][3],
        "final_amount": float(invoice[0][4]),  # Convert to float
        "status": invoice[0][5],
        "payment_status": invoice[0][6],
        "items": [
            {
                "name": p[0], 
                "quantity": p[1], 
                "unit": p[2],  
                "price": float(p[3]),  # Ensure float conversion
                "discount": float(p[4]),  
                "total": float(p[5])  
            }
            for p in products
        ]
    })

@app.route('/get_product_suggestions/<query>')
def get_product_suggestions(query):
    try:
        product_query = """
        SELECT product_id, product_name, price
        FROM inventory 
        WHERE product_name ILIKE %s AND availability >=2
        LIMIT 10
        """
        products = fetch_data(product_query, (f'%{query}%',))
        return jsonify({
            "products": [
                {
                    "product_id": p[0],
                    "product_name": p[1],
                    "price": float(p[2]),
                    "discount": 0  # Default to 0 since discount isn’t in inventory
                }
                for p in products
            ]
        })
    except Exception as e:
        logging.error(f"Error fetching product suggestions: {str(e)}")
        return jsonify({"products": []}), 500



# Route for filtering data
@app.route('/filters')
def filters_page():
    return render_template('filters.html')

# Route for order history
@app.route('/order_history')
def order_history():
    return render_template('order_history.html')

    



@app.route('/get_customer_details/<mobile>', methods=['GET'])
def get_customer_details(mobile):
    customer_query = "SELECT id, name, address, mail FROM customer WHERE mobile = %s"
    customer_data = fetch_data(customer_query, (mobile,))

    if customer_data:
        return jsonify({
            'exists': True,
            'id': customer_data[0][0],
            'name': customer_data[0][1],
            'address': customer_data[0][2],
            'mail': customer_data[0][3]
        })
    else:
        return jsonify({'exists': False})




@app.route('/get_product_details', methods=['GET'])
def get_product_details():
    try:
        product_id = request.args.get("id")
        product_name = request.args.get("name")

        if not product_id and not product_name:
            return jsonify(None), 400  # Bad request if no parameters provided

        # Define the query based on the provided parameter
        if product_id:
            query = """
            SELECT product_id, product_name, price 
            FROM inventory 
            WHERE product_id = %s AND availability >=2
            """
            params = (product_id,)
        elif product_name:
            query = """
            SELECT product_id, product_name, price 
            FROM inventory 
            WHERE product_name = %s AND availability >=2
            """
            params = (product_name,)

        product_details = fetch_data(query, params)

        if product_details:
            p = product_details[0]
            return jsonify({
                'product_id': p[0],
                'product_name': p[1],
                'price': float(p[2]),  # Ensure price is a float
                'discount': 0  # Default to 0 since discount isn’t in inventory
            })
        else:
            return jsonify(None), 404  # Not found if no matching product

    except Exception as e:
        logging.error(f"Error fetching product details: {str(e)}")
        return jsonify({'error': str(e)}), 500








@app.route('/inventory')
def inventory():
    # SQL query to fetch inventory data with COALESCE to handle NULL values
    query = '''
    SELECT 
        product_id,        -- pro_id (index 0)
        product_name,      -- pro_name (index 1)
        COALESCE(quantity, 0) AS quantity,  -- Ensure no NULL values (index 2)
        COALESCE(unit, 'pcs') AS unit,      -- Default to 'pcs' (index 3)
        COALESCE(category, 'General') AS category,  -- Default category (index 4)
        COALESCE(hscode, 'Unknown') AS hscode,      -- Default HS Code (index 5)
        COALESCE(expiry_date, '1900-01-01') AS expiry_date,  -- Default expiry date (index 6)
        COALESCE(availability, 1) AS availability  -- Default to 1 (Out of Stock) if NULL (index 7)
    FROM inventory;
    '''
    
    # Fetch inventory data
    inventory_data = fetch_data(query)

    # Debugging step: Print fetched data and verify structure
    print("Inventory Data:", inventory_data)
    if inventory_data:
        print("Number of columns:", len(inventory_data[0]))
    else:
        print("No data returned from inventory table")

    return render_template('inventory.html', data=inventory_data)




""" @app.route('/edit/<int:item_id>', methods=['GET', 'POST'])
def edit_inventory(item_id):
    if request.method == 'POST':
        # Fetch data from the form
        quantity = request.form['quantity']
        unit = request.form['unit']
        category = request.form['category']
        hs_code = request.form['hs_code']
        expiry_date = request.form['expiry_date']
        availability = 1 if request.form.get('availability') == 'on' else 0  # Convert checkbox to Boolean

        # SQL query to update the inventory item
        query = ""
        UPDATE inventory
        SET quantity=%s, unit=%s, category=%s, hscode=%s, expiry_date=%s, availability=%s
        WHERE product_id=%s
        ""
        execute_query(query, (quantity, unit, category, hs_code, expiry_date, availability, item_id))
        flash("Inventory item updated successfully!", "success")
        return redirect(url_for('inventory'))

    # SQL query to fetch the specific inventory item for editing
    query = ""
    SELECT i.product_id, a.pro_name, i.quantity, i.unit, 
           i.category, i.hscode, i.expiry_date, i.availability
    FROM inventory i
    JOIN add_product a ON i.product_id = a.pro_id
    WHERE i.product_id=%s
    ""
    item_data = fetch_data(query, (item_id,))
    if not item_data:
        flash("Item not found!", "danger")
        return redirect(url_for('inventory'))

    # Render the template with the data for editing
    return render_template('edit_inventory.html', item=item_data[0])
 """

""" @app.route('/delete/<int:item_id>', methods=['POST'])
def delete_inventory(item_id):
    try:
        # SQL query to delete the item from inventory
        query = "DELETE FROM inventory WHERE product_id=%s"
        execute_query(query, (item_id,))
        flash("Item deleted successfully!", "success")
    except Exception as e:
        flash(f"Error deleting item: {str(e)}", "danger")

    return redirect(url_for('inventory')) """

from datetime import datetime


@app.route('/add_item', methods=['GET', 'POST'])
def add_item():
    product_id = request.args.get('product_id')

    if not product_id:
        flash("Product ID is missing!", "danger")
        return redirect(url_for('inventory'))

    # Fetch product details from inventory and add_product
    product_query = """
    SELECT a.pro_name, i.unit, i.category, i.hscode, a.supplier_id, a.cost_price
    FROM inventory i
    JOIN add_product a ON i.product_id = a.pro_id
    WHERE i.product_id = %s
    """
    product = fetch_data(product_query, (product_id,))

    if not product:
        flash("Product not found in inventory!", "danger")
        return redirect(url_for('inventory'))

    # Unpack product data
    product_name, unit, category, hscode, supplier_id, cost_price = product[0]

    # Generate invoice number (format: YYYY00001)
    current_year = datetime.now().year
    invoice_query = "SELECT invoice_no FROM supplier_invoice WHERE invoice_no LIKE %s ORDER BY invoice_no DESC LIMIT 1"
    last_invoice = fetch_data(invoice_query, (f"{current_year}%",))

    last_number = int(last_invoice[0][0][-5:]) + 1 if last_invoice else 1
    invoice_no = f"{current_year}{last_number:05d}"  # Ensures 5-digit format (YYYY00001)

    if request.method == 'POST':
        try:
            quantity_added = int(request.form.get('quantity_added'))
            comments = request.form.get('comments', '')

            if quantity_added <= 0:
                flash("Quantity must be greater than 0!", "danger")
                return redirect(url_for('add_item', product_id=product_id))

        except (ValueError, TypeError):
            flash("Invalid quantity entered!", "danger")
            return redirect(url_for('add_item', product_id=product_id))

        # Calculate total amount
        total_amount = cost_price * quantity_added

        # Insert into add_item table and retrieve new item_id
        add_item_query = """
        INSERT INTO add_item (product_id, quantity_added, supplier_id, invoice_no, comments)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING item_id
        """
        result = execute_query(add_item_query, (product_id, quantity_added, supplier_id, invoice_no, comments), fetch=True)

        if result:
            item_id = result[0][0]

            # Insert into supplier_invoice table
            invoice_query = """
            INSERT INTO supplier_invoice (invoice_no, supplier_id, product_id, quantity, total_amount)
            VALUES (%s, %s, %s, %s, %s)
            """
            execute_query(invoice_query, (invoice_no, supplier_id, product_id, quantity_added, total_amount))

            flash("Item and supplier invoice added successfully!", "success")
            return redirect(url_for('inventory'))

        else:
            flash("Error adding item.", "danger")

    return render_template(
        'add_item.html',
        product_name=product_name,
        unit=unit,
        category=category,
        hscode=hscode,
        supplier_id=supplier_id,
        product_id=product_id,
        invoice_no=invoice_no
    )















@app.route('/payments')
def payments():
    query = """
    SELECT 
        p.invoice_no,  
        p.invoice_date, 
        i.total_amount, 
        p.amount, 
        p.payment_mode, 
        p.status 
    FROM payment p
    JOIN invoice i ON p.invoice_no = i.invoice_no
    """
    
    raw_data = fetch_data(query)
    payments_data = [
        {
            "invoice_no": row[0],
            "invoice_date": row[1].strftime("%Y-%m-%d"),
            "total_amount": row[2],
            "amount": row[3],
            "payment_mode": row[4],
            "status": row[5]
        } for row in raw_data
    ]
    
    print("Processed Payments Data:", payments_data)  # Debugging line
    
    return render_template('payments.html', data=payments_data)





""" from decimal import Decimal  # Import Decimal
@app.route('/ledger')
def ledger():
    query = ""
        SELECT 
            t.transaction_date,
            t.customer_mobile,  -- Replaced invoice_no
            COALESCE(c.name, 'Unknown') AS customer_name,
            t.amount,
            t.transaction_type,
            t.balance
        FROM ledger t
        LEFT JOIN customer c ON t.customer_mobile = c.mobile  -- Join on mobile instead of id
        ORDER BY t.transaction_date DESC
    ""
    ledger_data = fetch_data(query)
    return render_template('ledger.html', data=ledger_data) """
from decimal import Decimal  # Import Decimal

@app.route('/ledger')
def ledger():
    query = """
        SELECT 
            t.transaction_date,
            t.customer_mobile,
            COALESCE(c.name, 'Unknown') AS customer_name,
            t.amount,
            t.transaction_type,
            t.balance
        FROM ledger t
        LEFT JOIN customer c ON t.customer_mobile = c.mobile  -- Fixed typo: 'customer t' to 'customer c'
        ORDER BY t.transaction_date DESC
    """
    ledger_data = fetch_data(query)
    return render_template('ledger.html', data=ledger_data)

# Route to Import Ledger from CSV/Excel
@app.route('/import_ledger', methods=['POST'])
def import_ledger():
    try:
        if 'file' not in request.files:
            flash("No file selected!", "danger")
            return redirect(url_for('ledger'))

        file = request.files['file']

        if file.filename == '':
            flash("No selected file!", "danger")
            return redirect(url_for('ledger'))

        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)

            if filename.endswith('.csv'):
                df = pd.read_csv(filepath)
            else:
                df = pd.read_excel(filepath)

            for _, row in df.iterrows():
                execute_query("""
                    INSERT INTO ledger (transaction_date, invoice_no, supplier_id, amount, 
                                        transaction_type, description, balance, customer_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """, (row['transaction_date'], row['invoice_no'], row['supplier_id'], row['amount'], 
                      row['transaction_type'], row['description'], row['balance'], row['customer_id']))

            flash("Ledger imported successfully!", "success")
    except Exception as e:
        logging.error(f"Error importing ledger: {str(e)}")
        flash("Error importing ledger!", "danger")

    return redirect(url_for('ledger'))




@app.route('/add_ledger', methods=['GET', 'POST'])
def add_ledger():
    if request.method == 'POST':
        try:
            # Retrieve form data
            transaction_date = request.form.get('transaction_date')
            identifier_type = request.form.get('identifier_type')  # 'invoice' or 'mobile'
            invoice_no = request.form.get('invoice_no') if identifier_type == 'invoice' else None
            customer_mobile = request.form.get('customer_id') if identifier_type == 'mobile' else None
            amount = request.form.get('amount')
            transaction_type = request.form.get('transaction_type')  # 'Credit' or 'Debit'
            description = request.form.get('description', '')

            # Validate required fields
            if not transaction_date or not transaction_type or not amount:
                flash("Transaction date, transaction type, and amount are required.", 'danger')
                return redirect(url_for('add_ledger'))

            if not invoice_no and not customer_mobile:
                flash("Please enter a valid Invoice No. or Customer Mobile.", 'danger')
                return redirect(url_for('add_ledger'))

            # Validate amount before conversion
            try:
                amount = float(amount)
                if amount <= 0:
                    flash("Amount must be greater than zero.", 'danger')
                    return redirect(url_for('add_ledger'))
            except ValueError:
                flash("Invalid amount. Please enter a valid number.", 'danger')
                return redirect(url_for('add_ledger'))

            # Convert amount to Decimal for precision
            amount = Decimal(amount)

            # Fetch customer_id from mobile number
            customer_id = None
            if customer_mobile:
                query = "SELECT id FROM customer WHERE mobile = %s"
                customer_data = fetch_data(query, (customer_mobile,))
                if customer_data:
                    customer_id = customer_data[0][0]
                else:
                    flash(f"Customer with mobile number {customer_mobile} does not exist.", 'danger')
                    return redirect(url_for('add_ledger'))

            # Validate invoice if selected
            if invoice_no:
                invoice_check_query = "SELECT 1 FROM invoice WHERE invoice_no = %s"
                if not fetch_data(invoice_check_query, (invoice_no,)):
                    flash(f"Invoice number {invoice_no} does not exist.", 'danger')
                    return redirect(url_for('add_ledger'))

            # Fetch previous balance
            previous_balance = Decimal(0)  # Default if no previous transactions
            if customer_id:
                balance_query = "SELECT balance FROM ledger WHERE customer_id = %s ORDER BY transaction_date DESC LIMIT 1"
                previous_balance_data = fetch_data(balance_query, (customer_id,))
                if previous_balance_data and previous_balance_data[0][0] is not None:
                    previous_balance = Decimal(previous_balance_data[0][0])

            # Determine new balance
            new_balance = previous_balance + amount if transaction_type == 'Credit' else previous_balance - amount

            # Insert into Ledger
            insert_query = """
                INSERT INTO ledger (transaction_date, invoice_no, customer_id, amount, transaction_type, description, balance)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """
            params = (transaction_date, invoice_no, customer_id, amount, transaction_type, description, new_balance)
            result = execute_query(insert_query, params)

            if result:
                flash(f"Ledger transaction added successfully as {transaction_type}!", 'success')
                return redirect(url_for('ledger'))
            else:
                flash("An error occurred while adding the ledger transaction.", 'danger')

        except Exception as e:
            flash(f"An error occurred: {str(e)}", 'danger')

        return redirect(url_for('add_ledger'))

    # Fetch customers and invoices for dropdowns
    customers = fetch_data("SELECT id, name FROM customer")  
    invoices = fetch_data("SELECT invoice_no FROM invoice")  

    return render_template('add_ledger.html', customers=customers, invoices=invoices)






    
    


    
















# Error handling for 404
@app.errorhandler(404)
def page_not_found(error):
    return render_template('404.html'), 404


if __name__ == '__main__':
    app.run(debug=True)