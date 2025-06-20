-- Customer Table
CREATE TABLE customer (
    id SERIAL PRIMARY KEY,
    mobile VARCHAR(15) NOT NULL,
    name VARCHAR(100) NOT NULL,
    address TEXT,
    mail VARCHAR(100),
    time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Supplier Table
CREATE TABLE supplier (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    terms TEXT,
    mobile VARCHAR(15),
    mail VARCHAR(100),
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    company VARCHAR(255)
);

-- Product Table
CREATE TABLE add_product (
    pro_id BIGINT PRIMARY KEY GENERATED BY DEFAULT AS IDENTITY,
    pro_name VARCHAR(100) NOT NULL,
    cost_price NUMERIC(10, 2) NOT NULL,
    selling_price NUMERIC(10, 2) NOT NULL,
    net_profit NUMERIC(10, 2) GENERATED ALWAYS AS (selling_price - cost_price) STORED,
    profit_percentage NUMERIC(5, 2) GENERATED ALWAYS AS ((selling_price - cost_price) / cost_price * 100) STORED,
    quantity INT NOT NULL,
    unit VARCHAR(50) NOT NULL,
    category VARCHAR(100) NOT NULL,
    safety_level VARCHAR(50),
    expiry_date DATE,
    supplier_id INT REFERENCES supplier(id) ON DELETE SET NULL,
    supplier_name VARCHAR(100),
    terms_from_invoice TEXT,
    hc_code VARCHAR(50),
    CONSTRAINT unique_pro_id UNIQUE (pro_id)
);

-- Inventory Table
CREATE TABLE inventory (
    product_id BIGINT PRIMARY KEY REFERENCES add_product(pro_id) ON DELETE CASCADE,
    product_name VARCHAR(100),
    price NUMERIC(10, 2),
    amount NUMERIC(10, 2),
    unit VARCHAR(50) NOT NULL DEFAULT 'pcs',
    safety_level INT,
    category VARCHAR(100) NOT NULL DEFAULT 'General',
    hscode VARCHAR(50),
    expiry_date DATE,
    availability INT CHECK (availability BETWEEN 1 AND 5),
    quantity INT DEFAULT 0
);

-- Item Addition Table
CREATE TABLE add_item (
    item_id SERIAL PRIMARY KEY,
    product_id BIGINT REFERENCES add_product(pro_id) ON DELETE CASCADE,
    quantity_added INT NOT NULL CHECK (quantity_added > 0),
    added_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    supplier_id INT REFERENCES supplier(id) ON DELETE SET NULL,
    invoice_no VARCHAR(50),
    comments TEXT
);

-- Invoice Table
CREATE TABLE invoice (
    invoice_no BIGINT PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    bill_no INT UNIQUE,
    customer_id INT REFERENCES customer(id) ON DELETE SET NULL,
    customer_name VARCHAR(255) NOT NULL,
    customer_mobile VARCHAR(15) NOT NULL,
    date DATE NOT NULL,
    terms TEXT,
    sale_by VARCHAR(100),
    total_amount NUMERIC(10, 2) NOT NULL,
    discount NUMERIC(10, 2) DEFAULT 0,
    final_amount NUMERIC(10, 2) GENERATED ALWAYS AS (total_amount - discount) STORED,
    status VARCHAR(50) DEFAULT 'Pending',
    payment_status VARCHAR(50) DEFAULT 'Unpaid',
    total_paid DECIMAL(10,2) DEFAULT 0
);

-- Supplier Invoice Table
CREATE TABLE supplier_invoice (
    invoice_id SERIAL PRIMARY KEY,
    invoice_no VARCHAR(50) UNIQUE NOT NULL,
    supplier_id INT REFERENCES supplier(id) ON DELETE SET NULL,
    product_id INT REFERENCES add_product(pro_id) ON DELETE CASCADE,
    quantity INT NOT NULL CHECK (quantity > 0),
    total_amount DECIMAL(10,2) NOT NULL CHECK (total_amount > 0),
    invoice_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status VARCHAR(50) DEFAULT 'Pending',
    payment_status VARCHAR(50) DEFAULT 'Unpaid'
);

-- Invoice Products Table
CREATE TABLE invoice_products (
    id BIGINT PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    invoice_no BIGINT REFERENCES invoice(invoice_no) ON DELETE CASCADE,
    product_id BIGINT REFERENCES add_product(pro_id) ON DELETE CASCADE,
    quantity INT NOT NULL,
    unit VARCHAR(50),
    price NUMERIC(10, 2) NOT NULL,
    discount NUMERIC(10, 2) DEFAULT 0,
    total NUMERIC(10, 2) GENERATED ALWAYS AS ((price * quantity) - discount) STORED
);

-- Payment Table
CREATE TABLE payment (
    id SERIAL PRIMARY KEY,
    invoice_no BIGINT REFERENCES invoice(invoice_no) ON DELETE CASCADE,
    invoice_date DATE NOT NULL,
    amount NUMERIC(10, 2) NOT NULL,
    payment_receipt TEXT,
    receipt_no VARCHAR(100),
    customer_name VARCHAR(100),
    payment_mode VARCHAR(50),
    status VARCHAR(50),
    customer_id BIGINT
);

ALTER TABLE customer ADD CONSTRAINT unique_mobile UNIQUE (mobile);
-- Ledger Table
CREATE TABLE ledger (
    id SERIAL PRIMARY KEY,
    transaction_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    customer_mobile VARCHAR(15) NOT NULL,  -- New primary identifier
    customer_id BIGINT,  -- Keeping this for reference to customer table
    amount NUMERIC(10, 2) NOT NULL,
    transaction_type VARCHAR(50) NOT NULL CHECK (transaction_type IN ('Debit', 'Credit')),
    description TEXT,
    balance NUMERIC(10, 2) DEFAULT 0,
    FOREIGN KEY (customer_mobile) REFERENCES customer(mobile) ON DELETE CASCADE
);





ALTER TABLE invoice ADD COLUMN IF NOT EXISTS payment_method VARCHAR(50) DEFAULT 'unpaid';

UPDATE add_product SET safety_level = NULL WHERE safety_level = '';

ALTER TABLE inventory ALTER COLUMN safety_level TYPE INT USING safety_level::INT;
UPDATE inventory SET unit = 'pcs' WHERE unit IS NULL;
UPDATE inventory SET category = 'General' WHERE category IS NULL;

ALTER SEQUENCE invoice_invoice_no_seq RESTART WITH 100000;




ALTER TABLE payment DROP CONSTRAINT payment_pkey; 




