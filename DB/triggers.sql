-- Function to add a new product to inventory
CREATE OR REPLACE FUNCTION add_product_to_inventory()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO inventory (product_id, product_name, price, quantity, unit, category, safety_level, expiry_date, hscode)
    VALUES (
        NEW.pro_id, 
        NEW.pro_name, 
        NEW.selling_price, 
        NEW.quantity, 
        NEW.unit, 
        NEW.category, 
        CAST(NEW.safety_level AS INTEGER),  -- Ensure explicit casting
        NEW.expiry_date, 
        NEW.hc_code
    )
    ON CONFLICT (product_id) DO NOTHING;  -- Prevent duplicate insertions
    
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Drop the trigger if it exists
DROP TRIGGER IF EXISTS trg_add_product_inventory ON add_product;

-- Trigger to execute after inserting a new product
CREATE TRIGGER trg_add_product_inventory
AFTER INSERT ON add_product
FOR EACH ROW
EXECUTE FUNCTION add_product_to_inventory();








-- Function to update inventory quantity when a new product is added
CREATE OR REPLACE FUNCTION update_inventory_from_add_product()
RETURNS TRIGGER AS $$
BEGIN
    -- If product exists in inventory, update the quantity
    IF EXISTS (SELECT 1 FROM inventory WHERE product_id = NEW.pro_id) THEN
        UPDATE inventory
        SET quantity = quantity + NEW.quantity
        WHERE product_id = NEW.pro_id;
    ELSE
        -- Insert the product into inventory if it doesn't exist
        INSERT INTO inventory (product_id, product_name, quantity, unit, category, expiry_date, hscode)
        VALUES (
            NEW.pro_id,
            NEW.pro_name,
            NEW.quantity,
            NEW.unit,
            NEW.category,
            NEW.expiry_date,
            NEW.hc_code
        );
    END IF;
    
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Trigger to execute after inserting a new product into add_product
CREATE TRIGGER trg_update_inventory_from_add_product
AFTER INSERT ON add_product
FOR EACH ROW
EXECUTE FUNCTION update_inventory_from_add_product();









/* CREATE OR REPLACE FUNCTION deduct_inventory_on_sale()
RETURNS TRIGGER AS $$
DECLARE 
    available_stock INT;
BEGIN
    -- Check stock before deducting
    SELECT quantity INTO available_stock FROM inventory WHERE product_id = NEW.product_id;

    IF available_stock IS NULL THEN
        RAISE EXCEPTION 'Product not found in inventory!';
    ELSIF available_stock < NEW.quantity THEN
        RAISE EXCEPTION 'Stock is not sufficient for sale!';
    END IF;

    -- Deduct stock
    UPDATE inventory
    SET quantity = quantity - NEW.quantity
    WHERE product_id = NEW.product_id;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_deduct_inventory_sale
AFTER INSERT ON sale_product
FOR EACH ROW
EXECUTE FUNCTION deduct_inventory_on_sale(); */








CREATE OR REPLACE FUNCTION update_payment_status()
RETURNS TRIGGER AS $$
BEGIN
    -- If total_paid is NULL or 0, mark as 'Unpaid' and 'Pending'
    IF NEW.total_paid IS NULL OR NEW.total_paid = 0 THEN
        NEW.payment_status := 'Unpaid';
        NEW.status := 'Pending';
    ELSE
        -- If there's any payment, mark as 'Paid' and 'Completed'
        NEW.payment_status := 'Paid';
        NEW.status := 'Completed';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Drop the existing trigger if it exists
DROP TRIGGER IF EXISTS trigger_update_payment_status ON invoice;

-- Create the trigger that will fire before INSERT or UPDATE on the invoice table
CREATE TRIGGER trigger_update_payment_status
BEFORE INSERT OR UPDATE ON invoice
FOR EACH ROW
EXECUTE FUNCTION update_payment_status();






/* CREATE OR REPLACE FUNCTION insert_invoice_products_on_sale()
RETURNS TRIGGER AS $$
DECLARE
    product_unit VARCHAR(50);
BEGIN
    -- Get unit from add_product
    SELECT unit INTO product_unit FROM add_product WHERE pro_id = NEW.product_id;

    -- Insert into invoice_products table
    INSERT INTO invoice_products (invoice_no, product_id, quantity, unit, price, discount)
    VALUES (
        NEW.invoice_no, 
        NEW.product_id, 
        NEW.quantity, 
        COALESCE(product_unit, 'pcs'), 
        NEW.price, 
        COALESCE(NEW.discount, 0)
    );
    
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_invoice_products_sale ON sale_product;

-- Create trigger to execute function after inserting into sale_product
CREATE TRIGGER trg_invoice_products_sale
AFTER INSERT ON sale_product
FOR EACH ROW
EXECUTE FUNCTION insert_invoice_products_on_sale(); */










/* CREATE OR REPLACE FUNCTION update_ledger_on_sale()
RETURNS TRIGGER AS $$
BEGIN
    -- If the invoice is "Unpaid", record as "Debit"
    IF TRIM(LOWER(NEW.payment_status)) = 'unpaid' THEN
        -- Insert only if the invoice does not already exist in the ledger
        IF NOT EXISTS (SELECT 1 FROM ledger WHERE invoice_no = NEW.invoice_no) THEN
            INSERT INTO ledger (transaction_date, invoice_no, customer_id, amount, transaction_type, description)
            VALUES (CURRENT_TIMESTAMP, NEW.invoice_no, NEW.customer_id, COALESCE(NEW.final_amount, 0), 'Debit', 'Unpaid invoice for customer');
        END IF;

    -- If the invoice is "Paid", update ledger as "Credit"
    ELSIF TRIM(LOWER(NEW.payment_status)) = 'paid' THEN
        -- Check if an unpaid entry exists, then update it to "Credit"
        IF EXISTS (SELECT 1 FROM ledger WHERE invoice_no = NEW.invoice_no AND transaction_type = 'Debit') THEN
            UPDATE ledger 
            SET transaction_date = CURRENT_TIMESTAMP,
                transaction_type = 'Credit',
                description = 'Payment received for invoice'
            WHERE invoice_no = NEW.invoice_no;
        ELSE
            -- If no previous debit entry exists, insert a new "Credit" entry
            INSERT INTO ledger (transaction_date, invoice_no, customer_id, amount, transaction_type, description)
            VALUES (CURRENT_TIMESTAMP, NEW.invoice_no, NEW.customer_id, COALESCE(NEW.final_amount, 0), 'Credit', 'Paid invoice for customer');
        END IF;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;


-- Drop trigger if it exists
DROP TRIGGER IF EXISTS trg_update_ledger_sale ON invoice;

-- Create a new trigger
CREATE TRIGGER trg_update_ledger_sale
AFTER INSERT OR UPDATE ON invoice
FOR EACH ROW
EXECUTE FUNCTION update_ledger_on_sale(); */







/* CREATE OR REPLACE FUNCTION check_low_stock()
RETURNS TRIGGER AS $$
DECLARE
    safety INT;
BEGIN
    -- Fetch the safety level, defaulting to 0 if NULL or invalid
    SELECT COALESCE(NULLIF(safety_level, '')::INT, 0) INTO safety 
    FROM inventory 
    WHERE product_id = NEW.product_id;

    -- Only show warning if stock drops below safety level
    IF NEW.quantity < safety THEN
        RAISE NOTICE 'Warning: Stock for product ID % is below the safety level!', NEW.product_id;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_check_low_stock ON inventory;

CREATE TRIGGER trg_check_low_stock
AFTER UPDATE ON inventory
FOR EACH ROW
WHEN (NEW.quantity < OLD.quantity AND NEW.quantity < COALESCE(NULLIF(NEW.safety_level, '')::INT, 0))  -- Only trigger when stock falls below safety level
EXECUTE FUNCTION check_low_stock();
 */


/* 
CREATE OR REPLACE FUNCTION deduct_stock_on_sale()
RETURNS TRIGGER AS $$
DECLARE
    available_stock INT;
BEGIN
    -- Lock inventory row and check stock before deduction
    SELECT quantity INTO available_stock 
    FROM inventory 
    WHERE product_id = NEW.product_id 
    FOR UPDATE SKIP LOCKED;

    -- Validate stock availability
    IF available_stock IS NULL THEN
        RAISE EXCEPTION 'Product ID % not found in inventory!', NEW.product_id;
    ELSIF available_stock < NEW.quantity THEN
        RAISE EXCEPTION 'Insufficient stock for product ID %! Available: %, Required: %', 
            NEW.product_id, available_stock, NEW.quantity;
    END IF;

    -- Deduct stock
    UPDATE inventory
    SET quantity = quantity - NEW.quantity
    WHERE product_id = NEW.product_id;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Drop existing trigger if it exists
DROP TRIGGER IF EXISTS trg_deduct_stock_sale ON sale_product;

-- Create a new trigger to deduct stock when a sale is recorded
CREATE TRIGGER trg_deduct_stock_sale
AFTER INSERT ON sale_product
FOR EACH ROW
EXECUTE FUNCTION deduct_stock_on_sale();
 */





/* CREATE OR REPLACE FUNCTION insert_into_invoice_products()
RETURNS TRIGGER AS $$
DECLARE
    product_unit VARCHAR(50);
BEGIN
    -- Fetch unit from add_product, default to 'pcs' if NULL
    SELECT COALESCE(unit, 'pcs') INTO product_unit 
    FROM add_product 
    WHERE pro_id = NEW.product_id;

    -- Validate invoice existence
    IF NOT EXISTS (SELECT 1 FROM invoice WHERE invoice_no = NEW.invoice_no) THEN
        RAISE EXCEPTION 'Invoice No % does not exist!', NEW.invoice_no;
    END IF;

    -- Insert into invoice_products
    INSERT INTO invoice_products (invoice_no, product_id, quantity, unit, price, discount)
    VALUES (
        NEW.invoice_no, 
        NEW.product_id, 
        NEW.quantity, 
        product_unit, 
        NEW.price, 
        COALESCE(NEW.discount, 0)
    );

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Drop existing trigger if it exists
DROP TRIGGER IF EXISTS trg_insert_invoice_products ON sale_product;

-- Create a new trigger to insert into invoice_products after a sale
CREATE TRIGGER trg_insert_invoice_products
AFTER INSERT ON sale_product
FOR EACH ROW
EXECUTE FUNCTION insert_into_invoice_products();
 */






/* CREATE OR REPLACE FUNCTION update_invoice_total_on_sale()
RETURNS TRIGGER AS $$
DECLARE
    new_total NUMERIC(10,2);
BEGIN
    -- Calculate the new total amount from invoice_products
    SELECT COALESCE(SUM(total), 0) INTO new_total 
    FROM invoice_products 
    WHERE invoice_no = NEW.invoice_no;

    -- Update the invoice table with the new total
    UPDATE invoice
    SET total_amount = new_total
    WHERE invoice_no = NEW.invoice_no;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;


-- Drop existing triggers if they exist
DROP TRIGGER IF EXISTS trg_update_invoice_total_insert ON sale_product;
DROP TRIGGER IF EXISTS trg_update_invoice_total_update ON sale_product;
DROP TRIGGER IF EXISTS trg_update_invoice_total_delete ON sale_product;

-- Create trigger to update invoice total after inserting into sale_product
CREATE TRIGGER trg_update_invoice_total_insert
AFTER INSERT ON sale_product
FOR EACH ROW
EXECUTE FUNCTION update_invoice_total_on_sale();

-- Create trigger to update invoice total after updating sale_product
CREATE TRIGGER trg_update_invoice_total_update
AFTER UPDATE ON sale_product
FOR EACH ROW
EXECUTE FUNCTION update_invoice_total_on_sale();

-- Create trigger to update invoice total after deleting from sale_product
CREATE TRIGGER trg_update_invoice_total_delete
AFTER DELETE ON sale_product
FOR EACH ROW
EXECUTE FUNCTION update_invoice_total_on_sale(); */


-- 
-- 
-- 

CREATE OR REPLACE FUNCTION update_invoice_payment_status()
RETURNS TRIGGER AS $$
DECLARE
    total_paid_amount NUMERIC(10,2);
    invoice_total NUMERIC(10,2);
    invoice_id BIGINT;
BEGIN
    -- Get invoice number (handles DELETE case where NEW is NULL)
    invoice_id := COALESCE(NEW.invoice_no, OLD.invoice_no);

    -- Fetch the total invoice amount
    SELECT COALESCE(total_amount, 0) 
    INTO invoice_total 
    FROM invoice 
    WHERE invoice_no = invoice_id;

    -- Compute total paid amount for the invoice
    SELECT COALESCE(SUM(amount), 0) 
    INTO total_paid_amount
    FROM payment
    WHERE invoice_no = invoice_id;

    -- Update invoice with new payment status
    UPDATE invoice
    SET 
        total_paid = total_paid_amount, 
        payment_status = CASE
            WHEN total_paid_amount >= invoice_total THEN 'Paid'
            WHEN total_paid_amount > 0 THEN 'Partially Paid'
            ELSE 'Unpaid'
        END
    WHERE invoice_no = invoice_id;

    -- Return the correct row depending on operation type
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    ELSE
        RETURN NEW;
    END IF;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_update_invoice_payment_status_insert ON payment;
DROP TRIGGER IF EXISTS trg_update_invoice_payment_status_update ON payment;
DROP TRIGGER IF EXISTS trg_update_invoice_payment_status_delete ON payment;

CREATE TRIGGER trg_update_invoice_payment_status_insert
AFTER INSERT ON payment
FOR EACH ROW
EXECUTE FUNCTION update_invoice_payment_status();

CREATE TRIGGER trg_update_invoice_payment_status_update
AFTER UPDATE ON payment
FOR EACH ROW
EXECUTE FUNCTION update_invoice_payment_status();

CREATE TRIGGER trg_update_invoice_payment_status_delete
AFTER DELETE ON payment
FOR EACH ROW
EXECUTE FUNCTION update_invoice_payment_status();



-- 
-- 
-- 
-- 



/* CREATE OR REPLACE FUNCTION log_payment_in_ledger()
RETURNS TRIGGER AS $$
DECLARE 
    previous_balance NUMERIC := 0;
    new_balance NUMERIC;
    cust_id INT;
    invoice_status TEXT;
BEGIN
    -- Ensure valid invoice number
    IF NEW.invoice_no IS NULL THEN
        RAISE EXCEPTION 'Cannot log payment: Invoice number is missing!';
    END IF;

    -- Ensure valid payment amount
    IF NEW.amount IS NULL OR NEW.amount <= 0 THEN
        RAISE EXCEPTION 'Invalid payment amount: Must be greater than zero!';
    END IF;

    -- Fetch customer_id and payment status from invoice table
    SELECT customer_id, payment_status INTO cust_id, invoice_status
    FROM invoice
    WHERE invoice_no = NEW.invoice_no;

    -- Ensure customer_id is valid
    IF cust_id IS NULL THEN
        RAISE EXCEPTION 'Cannot log payment: Customer ID not found for Invoice %!', NEW.invoice_no;
    END IF;

    -- Prevent duplicate payments if invoice is already marked as paid
    IF TRIM(LOWER(invoice_status)) = 'paid' THEN
        RAISE EXCEPTION 'Payment already recorded for Invoice %!', NEW.invoice_no;
    END IF;

    -- Fetch previous balance for the customer
    SELECT COALESCE(SUM(amount), 0) INTO previous_balance
    FROM ledger 
    WHERE customer_id = cust_id;

    -- Calculate new balance (reducing the outstanding amount)
    new_balance := previous_balance - NEW.amount;

    -- Ensure balance does not go negative
    IF new_balance < 0 THEN
        RAISE EXCEPTION 'Payment exceeds outstanding balance!';
    END IF;

    -- Insert payment record into ledger as "Credit"
    INSERT INTO ledger (
        transaction_date, invoice_no, customer_id, amount, transaction_type, description, balance
    ) VALUES (
        CURRENT_TIMESTAMP, 
        NEW.invoice_no, 
        cust_id, 
        NEW.amount, 
        'Credit',  -- Payment received, so it's recorded as Credit
        format('Payment received for Invoice %s', NEW.invoice_no),
        new_balance
    );

    -- Update invoice table to mark as "Paid" if balance is 0
    IF new_balance = 0 THEN
        UPDATE invoice
        SET payment_status = 'Paid'
        WHERE invoice_no = NEW.invoice_no;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;


-- Drop trigger if it exists
DROP TRIGGER IF EXISTS trg_log_payment_ledger ON payments;

-- Create a new trigger
CREATE TRIGGER trg_log_payment_ledger
AFTER INSERT ON payments
FOR EACH ROW
EXECUTE FUNCTION log_payment_in_ledger(); */








/* CREATE OR REPLACE FUNCTION update_ledger_balance()
RETURNS TRIGGER AS $$
DECLARE
    last_balance NUMERIC(10, 2) := 0;  -- Default to 0 if no balance is found
BEGIN
    -- Fetch the last recorded balance for the same invoice number
    SELECT balance 
    INTO last_balance
    FROM ledger
    WHERE invoice_number = NEW.invoice_number  -- Ensure balance is updated per invoice
    ORDER BY transaction_date DESC, id DESC
    LIMIT 1
    FOR UPDATE SKIP LOCKED;

    -- Set balance to 0 if no prior transactions exist for this invoice
    last_balance := COALESCE(last_balance, 0);

    -- Ensure valid transaction type
    IF NEW.transaction_type NOT IN ('Credit', 'Debit') THEN
        RAISE EXCEPTION 'Invalid transaction type. Must be "Credit" or "Debit".';
    END IF;

    -- Ensure amount is positive
    IF NEW.amount IS NULL OR NEW.amount < 0 THEN
        RAISE EXCEPTION 'Transaction amount must be greater than or equal to zero.';
    END IF;

    -- Calculate the new balance based on transaction type
    IF NEW.transaction_type = 'Credit' THEN
        NEW.balance := last_balance + NEW.amount;
    ELSIF NEW.transaction_type = 'Debit' THEN
        NEW.balance := last_balance - NEW.amount;
    END IF;

    -- Ensure balance is not NULL
    IF NEW.balance IS NULL THEN
        RAISE EXCEPTION 'Balance calculation failed for invoice number %.', NEW.invoice_number;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Drop the trigger if it already exists
DROP TRIGGER IF EXISTS ledger_balance_trigger ON ledger;

-- Create a new trigger that updates balance before inserting a new ledger entry
CREATE TRIGGER ledger_balance_trigger
BEFORE INSERT ON ledger
FOR EACH ROW
EXECUTE FUNCTION update_ledger_balance();
 */

CREATE OR REPLACE FUNCTION update_ledger_balance()
RETURNS TRIGGER AS $$
DECLARE
    last_balance NUMERIC(10, 2) := 0;  -- Default to 0 if no balance is found
    existing_transaction RECORD;
BEGIN
    -- Check if a transaction already exists for this invoice number
    SELECT * INTO existing_transaction 
    FROM ledger 
    WHERE invoice_number = NEW.invoice_number
    LIMIT 1;

    -- Prevent duplicate transactions for the same invoice number
    IF FOUND THEN
        RAISE EXCEPTION 'A transaction already exists for invoice number %. Each invoice must have a unique transaction.', NEW.invoice_number;
    END IF;

    -- Fetch the last recorded balance for the customer (not just the invoice)
    SELECT balance 
    INTO last_balance
    FROM ledger
    WHERE customer_id = NEW.customer_id
    ORDER BY transaction_date DESC, id DESC
    LIMIT 1
    FOR UPDATE SKIP LOCKED;

    -- Set balance to 0 if no prior transactions exist for this customer
    last_balance := COALESCE(last_balance, 0);

    -- Ensure valid transaction type
    IF NEW.transaction_type NOT IN ('Credit', 'Debit') THEN
        RAISE EXCEPTION 'Invalid transaction type. Must be "Credit" or "Debit".';
    END IF;

    -- Ensure amount is positive
    IF NEW.amount IS NULL OR NEW.amount < 0 THEN
        RAISE EXCEPTION 'Transaction amount must be greater than or equal to zero.';
    END IF;

    -- Calculate the new balance based on transaction type
    IF NEW.transaction_type = 'Credit' THEN
        NEW.balance := last_balance + NEW.amount;
    ELSIF NEW.transaction_type = 'Debit' THEN
        NEW.balance := last_balance - NEW.amount;
    END IF;

    -- Ensure balance is not NULL
    IF NEW.balance IS NULL THEN
        RAISE EXCEPTION 'Balance calculation failed for invoice number %.', NEW.invoice_number;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Drop the trigger if it already exists
DROP TRIGGER IF EXISTS ledger_balance_trigger ON ledger;

-- Create a new trigger that updates balance before inserting a new ledger entry
CREATE TRIGGER ledger_balance_trigger
BEFORE INSERT ON ledger
FOR EACH ROW
EXECUTE FUNCTION update_ledger_balance();







/* CREATE OR REPLACE FUNCTION update_inventory_on_add_item()
RETURNS TRIGGER AS $$
BEGIN
    -- Update the inventory table by adding the new quantity
    UPDATE inventory
    SET quantity = quantity + NEW.quantity_added
    WHERE product_id = NEW.product_id;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_update_inventory_add_item ON add_item;

CREATE TRIGGER trg_update_inventory_add_item
AFTER INSERT ON add_item
FOR EACH ROW
EXECUTE FUNCTION update_inventory_on_add_item(); */






/* CREATE OR REPLACE FUNCTION update_add_product_and_inventory()
RETURNS TRIGGER AS $$
DECLARE
    existing_pro_id BIGINT;
    valid_supplier_id INT;
BEGIN
    -- Validate supplier_id: Ensure it exists and is an integer
    SELECT id INTO valid_supplier_id FROM supplier WHERE id = NEW.supplier_id;

    IF valid_supplier_id IS NULL THEN
        RAISE EXCEPTION 'Invalid supplier_id: %', NEW.supplier_id;
    END IF;

    -- Check if product already exists in add_product
    SELECT pro_id INTO existing_pro_id FROM add_product WHERE pro_name = NEW.pro_name LIMIT 1;

    IF existing_pro_id IS NOT NULL THEN
        -- If product exists, update the quantity
        UPDATE add_product
        SET quantity = quantity + NEW.quantity
        WHERE pro_id = existing_pro_id;

        -- Update inventory accordingly
        UPDATE inventory
        SET quantity = quantity + NEW.quantity
        WHERE product_id = existing_pro_id;
        
        RETURN NULL; 
    ELSE
        -- Insert new product into add_product
        INSERT INTO add_product (pro_name, cost_price, selling_price, quantity, unit, category, 
                                 safety_level, expiry_date, supplier_id, name, terms_from_invoice, hc_code)
        VALUES (
            TRIM(NEW.pro_name),
            NEW.cost_price,
            NEW.selling_price,
            NEW.quantity,
            TRIM(NEW.unit),
            TRIM(NEW.category),
            NULLIF(NEW.safety_level, '')::INT,
            NEW.expiry_date,
            valid_supplier_id,  -- Use validated supplier_id
            TRIM(NEW.name),
            TRIM(NEW.terms_from_invoice),
            COALESCE(NEW.hc_code, '')
        )
        RETURNING pro_id INTO existing_pro_id;
        
        -- Insert into inventory
        INSERT INTO inventory (product_id, product_name, quantity, unit, category, expiry_date, hscode)
        VALUES (
            existing_pro_id,
            TRIM(NEW.pro_name),
            NEW.quantity,
            TRIM(NEW.unit),
            TRIM(NEW.category),
            NEW.expiry_date,
            COALESCE(NEW.hc_code, '')
        );
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Drop existing trigger if it exists
DROP TRIGGER IF EXISTS trigger_update_add_product_inventory ON add_product;

-- Create new trigger
CREATE TRIGGER trigger_update_add_product_inventory
BEFORE INSERT ON add_product
FOR EACH ROW
EXECUTE FUNCTION update_add_product_and_inventory(); */



/* CREATE OR REPLACE FUNCTION update_inventory_on_add_product()
RETURNS TRIGGER AS $$
BEGIN
    -- Insert into inventory if product_id does not exist, else update quantity
    INSERT INTO inventory (product_id, product_name, price, quantity, unit, category, expiry_date, safety_level)
    VALUES (NEW.pro_id, NEW.pro_name, NEW.selling_price, NEW.quantity, NEW.unit, NEW.category, NEW.expiry_date, 
            COALESCE(NEW.safety_level, 0))  -- Handle NULL safety_level
    ON CONFLICT (product_id) 
    DO UPDATE 
    SET quantity = inventory.quantity + EXCLUDED.quantity;
    
    -- Update the quantity in add_product
    UPDATE add_product
    SET quantity = quantity + NEW.quantity
    WHERE pro_id = NEW.pro_id;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Drop existing trigger if it exists
DROP TRIGGER IF EXISTS trg_update_inventory_add_product ON add_product;

-- Create new trigger
CREATE TRIGGER trg_update_inventory_add_product
AFTER INSERT ON add_product
FOR EACH ROW
EXECUTE FUNCTION update_inventory_on_add_product(); */





CREATE OR REPLACE FUNCTION update_inventory_on_add_item()
RETURNS TRIGGER AS $$
BEGIN
    -- Update the inventory table by adding the new quantity from add_item
    UPDATE inventory
    SET quantity = quantity + NEW.quantity_added
    WHERE product_id = NEW.product_id;
    
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_update_inventory_add_item ON add_item;

CREATE TRIGGER trg_update_inventory_add_item
AFTER INSERT ON add_item
FOR EACH ROW
EXECUTE FUNCTION update_inventory_on_add_item();


CREATE OR REPLACE FUNCTION update_add_product_on_inventory_change()
RETURNS TRIGGER AS $$
BEGIN
    -- Update the quantity in add_product whenever inventory is updated
    UPDATE add_product
    SET quantity = NEW.quantity
    WHERE pro_id = NEW.product_id;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Drop the trigger if it already exists
DROP TRIGGER IF EXISTS trg_update_add_product_on_inventory ON inventory;

-- Create a new trigger to update add_product when inventory quantity changes
CREATE TRIGGER trg_update_add_product_on_inventory
AFTER UPDATE ON inventory
FOR EACH ROW
EXECUTE FUNCTION update_add_product_on_inventory_change();
