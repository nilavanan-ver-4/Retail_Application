CREATE OR REPLACE FUNCTION insert_payment_on_invoice()
RETURNS TRIGGER AS $$
DECLARE
    customer_name TEXT;
    customer_id INT;
    payment_mode VARCHAR(50);
BEGIN
    -- Fetch customer_id and payment_mode from the newly inserted or updated invoice
    customer_id := NEW.customer_id;
    payment_mode := NEW.payment_mode;  -- Use NEW.payment_mode explicitly to avoid ambiguity

    -- Ensure customer_id exists
    IF customer_id IS NULL THEN
        RAISE EXCEPTION 'Cannot insert payment: No customer ID found for Invoice %!', NEW.invoice_no;
    END IF;

    -- Get customer name based on invoice's customer_id
    SELECT name INTO customer_name FROM customer WHERE id = customer_id;

    -- Handle missing or negative final_amount
    IF NEW.final_amount IS NULL OR NEW.final_amount <= 0 THEN
        NEW.final_amount := 0;  -- Store as 0 to avoid errors
    END IF;

    -- Only insert/update payment if payment_status is 'Paid' and final_amount is greater than 0
    IF NEW.payment_status = 'Paid' AND NEW.final_amount > 0 THEN
        -- Check if a payment already exists for this invoice
        IF NOT EXISTS (SELECT 1 FROM payment WHERE invoice_no = NEW.invoice_no) THEN
            -- Insert payment into payment table if it doesn't exist
            INSERT INTO payment (
                invoice_no,
                invoice_date,
                amount,
                payment_receipt,
                receipt_no,
                customer_name,
                payment_mode,
                status
            )
            VALUES (
                NEW.invoice_no,
                NEW.date,
                NEW.final_amount,
                NULL,  -- You can populate this field as needed
                NULL,  -- You can generate a receipt number here if necessary
                customer_name,
                payment_mode,  -- Use the payment_mode from the invoice
                'Completed'  -- Payment status as completed
            );
        ELSE
            -- Optionally, you can update the existing payment record if needed
            UPDATE payment
            SET amount = NEW.final_amount,
                payment_mode = NEW.payment_mode,  -- Explicitly use NEW.payment_mode here
                status = 'Completed'
            WHERE invoice_no = NEW.invoice_no;
        END IF;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Drop the existing trigger if it exists
DROP TRIGGER IF EXISTS trg_insert_payment_on_invoice ON invoice;

-- Create trigger to insert or update a payment after an invoice is created or updated
CREATE TRIGGER trg_insert_payment_on_invoice
AFTER INSERT OR UPDATE ON invoice
FOR EACH ROW
EXECUTE FUNCTION insert_payment_on_invoice();



















INSERT INTO payment (invoice_no, customer_id, invoice_date, amount, customer_name, payment_mode, status)
SELECT 
    invoice_no, 
    customer_id, 
    date AS invoice_date, 
    final_amount AS amount, 
    customer_name, 
    'Pending' AS payment_mode, 
    'Pending' AS status
FROM invoice
WHERE payment_status = 'Unpaid';


INSERT INTO payment (invoice_no, customer_id, invoice_date, amount, customer_name, payment_mode, status)
SELECT 
    invoice_no, 
    customer_id, 
    date AS invoice_date, 
    total_paid,  -- Inserts only the paid amount
    customer_name, 
    'Cash' AS payment_mode, 
    'Completed' AS status
FROM invoice
WHERE total_paid > 0;
