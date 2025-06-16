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


/* CREATE OR REPLACE FUNCTION update_ledger_on_invoice()
RETURNS TRIGGER AS $$
DECLARE
    previous_balance DECIMAL(10, 2) DEFAULT 0.0;
    transaction_amount DECIMAL(10, 2);
    transaction_type VARCHAR(10);
    transaction_description TEXT;
    new_balance DECIMAL(10, 2);
BEGIN
    -- Calculate the previous balance for the customer based on customer_mobile
    SELECT COALESCE(SUM(CASE 
                        WHEN ledger.transaction_type = 'Debit' THEN amount 
                        WHEN ledger.transaction_type = 'Credit' THEN -amount 
                        ELSE 0 
                    END), 0.0)
    INTO previous_balance
    FROM ledger
    WHERE customer_mobile = NEW.customer_mobile;

    -- Determine transaction type, amount, and description based on payment status
    IF NEW.payment_status = 'Paid' THEN
        -- For a fully paid invoice at creation, net balance should be 0 (Debit for invoice, Credit for payment)
        transaction_amount := NEW.final_amount;
        transaction_type := 'Credit'; -- Represents payment
        transaction_description := 'Payment Received (Full) - ' || NEW.customer_name || ' (Invoice #' || NEW.invoice_no || ')';
        -- If this is the first transaction and fully paid, balance should be 0, not negative
        new_balance := previous_balance; -- No change to balance since invoice and payment cancel out
        IF previous_balance = 0 THEN
            new_balance := 0.0; -- Ensure no negative balance for new customers
        ELSE
            new_balance := previous_balance - transaction_amount; -- Reduce existing debt
        END IF;
    ELSIF NEW.payment_status = 'Partially Paid' THEN
        transaction_amount := NEW.final_amount - NEW.total_paid; -- Amount still owed
        transaction_type := 'Debit';
        transaction_description := 'Invoice Creation (Partial Payment) - ' || NEW.customer_name || ' (Invoice #' || NEW.invoice_no || ')';
        new_balance := previous_balance + transaction_amount;
    ELSE  -- Unpaid
        transaction_amount := NEW.final_amount;
        transaction_type := 'Debit';
        transaction_description := 'Invoice Creation (Unpaid) - ' || NEW.customer_name || ' (Invoice #' || NEW.invoice_no || ')';
        new_balance := previous_balance + transaction_amount;
    END IF;

    -- Insert a single ledger entry
    INSERT INTO ledger (
        transaction_date,
        customer_mobile,
        customer_id,
        amount,
        transaction_type,
        description,
        balance
    )
    VALUES (
        CURRENT_TIMESTAMP,
        NEW.customer_mobile,
        NEW.customer_id,
        transaction_amount,
        transaction_type,
        transaction_description,
        new_balance
    );

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Reattach the trigger
DROP TRIGGER IF EXISTS trigger_update_ledger ON invoice;
CREATE TRIGGER trigger_update_ledger
AFTER INSERT ON invoice
FOR EACH ROW
EXECUTE FUNCTION update_ledger_on_invoice();

 */

CREATE OR REPLACE FUNCTION update_ledger_on_invoice()
RETURNS TRIGGER AS $$
DECLARE
    previous_balance DECIMAL(10, 2) DEFAULT 0.0;
BEGIN
    -- Calculate the previous balance for the customer based on customer_mobile
    SELECT COALESCE(SUM(CASE 
                        WHEN transaction_type = 'Debit' THEN amount 
                        WHEN transaction_type = 'Credit' THEN -amount 
                        ELSE 0 
                    END), 0.0)
    INTO previous_balance
    FROM ledger
    WHERE customer_mobile = NEW.customer_mobile;

    -- Step 1: Record the invoice creation as a Debit
    INSERT INTO ledger (
        transaction_date,
        customer_mobile,
        customer_id,
        amount,
        transaction_type,
        description,
        balance
    )
    VALUES (
        CURRENT_TIMESTAMP,
        NEW.customer_mobile,
        NEW.customer_id,
        NEW.final_amount,
        'Debit',
        'Invoice Creation - ' || NEW.customer_name || ' (Invoice #' || NEW.invoice_no || ')',
        previous_balance + NEW.final_amount
    );

    -- Step 2: Record payment if applicable
    IF NEW.payment_status = 'Paid' AND NEW.total_paid > 0 THEN
        INSERT INTO ledger (
            transaction_date,
            customer_mobile,
            customer_id,
            amount,
            transaction_type,
            description,
            balance
        )
        VALUES (
            CURRENT_TIMESTAMP + INTERVAL '1 second', -- Ensure order after Debit
            NEW.customer_mobile,
            NEW.customer_id,
            NEW.total_paid,
            'Credit',
            'Payment Received (Full) - ' || NEW.customer_name || ' (Invoice #' || NEW.invoice_no || ')',
            previous_balance + NEW.final_amount - NEW.total_paid
        );
    ELSIF NEW.payment_status = 'Partially Paid' AND NEW.total_paid > 0 THEN
        INSERT INTO ledger (
            transaction_date,
            customer_mobile,
            customer_id,
            amount,
            transaction_type,
            description,
            balance
        )
        VALUES (
            CURRENT_TIMESTAMP + INTERVAL '1 second',
            NEW.customer_mobile,
            NEW.customer_id,
            NEW.total_paid,
            'Credit',
            'Payment Received (Partial) - ' || NEW.customer_name || ' (Invoice #' || NEW.invoice_no || ')',
            previous_balance + NEW.final_amount - NEW.total_paid
        );
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Reattach the trigger
DROP TRIGGER IF EXISTS trigger_update_ledger ON invoice;
CREATE TRIGGER trigger_update_ledger
AFTER INSERT ON invoice
FOR EACH ROW
EXECUTE FUNCTION update_ledger_on_invoice();




CREATE OR REPLACE FUNCTION update_payment_on_invoice()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.payment_status = 'Paid' AND NEW.total_paid > 0 THEN
        INSERT INTO payment (
            invoice_no,
            invoice_date,
            amount,
            customer_name,
            payment_mode,
            status,
            customer_id
        )
        VALUES (
            NEW.invoice_no,
            NEW.date,
            NEW.total_paid,
            NEW.customer_name,
            NEW.payment_method,
            NEW.payment_status,
            NEW.customer_id
        );
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Recreate the trigger
DROP TRIGGER IF EXISTS trigger_update_payment ON invoice;
CREATE TRIGGER trigger_update_payment
AFTER INSERT ON invoice
FOR EACH ROW
EXECUTE FUNCTION update_payment_on_invoice();






-- Recreate the function
CREATE OR REPLACE FUNCTION update_payment_status()
RETURNS TRIGGER AS $$
BEGIN
    RAISE NOTICE 'Trigger Fired: total_paid=%, payment_method=%', NEW.total_paid, NEW.payment_method; -- Debug
    IF NEW.payment_method = 'unpaid' THEN
        NEW.payment_status := 'Unpaid';
        NEW.status := 'Pending';
        NEW.total_paid := 0.0;
    ELSIF NEW.total_paid > 0 AND NEW.payment_method IN ('cash', 'credit_card', 'bank_transfer','upi') THEN
        NEW.payment_status := 'Paid';
        NEW.status := 'Completed';
    ELSE
        NEW.payment_status := 'Unpaid';
        NEW.status := 'Pending';
        NEW.total_paid := 0.0;
    END IF;
    RAISE NOTICE 'Result: payment_status=%, status=%', NEW.payment_status, NEW.status; -- Debug
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;


DROP TRIGGER IF EXISTS trigger_update_payment_status ON invoice;

CREATE TRIGGER trigger_update_payment_status
BEFORE INSERT OR UPDATE ON invoice
FOR EACH ROW
EXECUTE FUNCTION update_payment_status();








CREATE OR REPLACE FUNCTION update_inventory_on_add_item()
RETURNS TRIGGER AS $$
BEGIN
    -- Check if the product exists in inventory
    IF EXISTS (SELECT 1 FROM inventory WHERE product_id = NEW.product_id) THEN
        -- Update the quantity
        UPDATE inventory
        SET quantity = quantity + NEW.quantity_added
        WHERE product_id = NEW.product_id;
    ELSE
        -- Optionally insert a new record (if business logic allows)
        -- You may need to fetch additional details from add_product or elsewhere
        RAISE NOTICE 'Product % not found in inventory. Consider adding it manually.', NEW.product_id;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Recreate the trigger
DROP TRIGGER IF EXISTS trg_update_inventory_add_item ON add_item;
CREATE TRIGGER trg_update_inventory_add_item
AFTER INSERT ON add_item
FOR EACH ROW
EXECUTE FUNCTION update_inventory_on_add_item();





CREATE OR REPLACE FUNCTION update_add_product_on_inventory_change()
RETURNS TRIGGER AS $$
BEGIN
    -- Check if the product exists in add_product
    IF EXISTS (SELECT 1 FROM add_product WHERE pro_id = NEW.product_id) THEN
        -- Update the quantity in add_product
        UPDATE add_product
        SET quantity = NEW.quantity
        WHERE pro_id = NEW.product_id;
    ELSE
        RAISE NOTICE 'Product % not found in add_product. No update performed.', NEW.product_id;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Recreate the trigger
DROP TRIGGER IF EXISTS trg_update_add_product_on_inventory ON inventory;
CREATE TRIGGER trg_update_add_product_on_inventory
AFTER UPDATE ON inventory
FOR EACH ROW
EXECUTE FUNCTION update_add_product_on_inventory_change();



CREATE OR REPLACE FUNCTION update_inventory_availability_scale()
RETURNS TRIGGER AS $$
DECLARE
    ratio NUMERIC;
BEGIN
    -- Cast safety_level from add_product (VARCHAR) to INT, default to 1 if NULL to avoid division by zero
    NEW.safety_level := COALESCE(CAST((SELECT safety_level FROM add_product WHERE pro_id = NEW.product_id) AS INT), 1);
    
    -- Calculate ratio (quantity / safety_level)
    ratio := CASE 
        WHEN NEW.safety_level = 0 THEN 1.0 -- Avoid division by zero
        ELSE NEW.quantity::NUMERIC / NEW.safety_level
    END;
    
    -- Map to 1-5 scale with new thresholds
    NEW.availability := CASE
        WHEN ratio >= 1.0 THEN 5       -- In Stock (100% or more of safety_level)
        WHEN ratio >= 0.85 THEN 4      -- In Stock (85% to <100% of safety_level)
        WHEN ratio >= 0.50 THEN 3      -- Low Stock (50% to <85% of safety_level)
        WHEN ratio >= 0.25 THEN 2      -- Low Stock (25% to <50% of safety_level)
        ELSE 1                         -- Out of Stock (<25% of safety_level)
    END;
    
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_update_availability ON inventory;

CREATE TRIGGER trigger_update_availability
BEFORE INSERT OR UPDATE OF quantity, safety_level
ON inventory
FOR EACH ROW
EXECUTE FUNCTION update_inventory_availability_scale();