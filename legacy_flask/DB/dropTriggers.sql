DROP TRIGGER IF EXISTS trg_update_add_product_quantity ON add_item;
DROP TRIGGER IF EXISTS trg_update_inventory_quantity ON add_item;
DROP TRIGGER IF EXISTS trg_add_product_inventory ON add_product;
DROP TRIGGER IF EXISTS trg_update_inventory_product ON add_product;
DROP TRIGGER IF EXISTS trg_check_low_stock ON inventory;
DROP TRIGGER IF EXISTS trg_delete_payment_on_invoice_delete ON invoice;
DROP TRIGGER IF EXISTS trg_insert_payment_on_invoice ON invoice;
DROP TRIGGER IF EXISTS trg_update_ledger_sale ON invoice;
DROP TRIGGER IF EXISTS trg_update_payment_on_invoice_change ON invoice;
DROP TRIGGER IF EXISTS trg_check_full_payment ON payment;
DROP TRIGGER IF EXISTS trg_check_payment_amount ON payment;
DROP TRIGGER IF EXISTS trg_log_payment_ledger ON payment;
DROP TRIGGER IF EXISTS trg_update_invoice_on_payment ON payment;
DROP TRIGGER IF EXISTS trg_update_invoice_payment ON payment;
DROP TRIGGER IF EXISTS trg_deduct_inventory_sale ON sale_product;
DROP TRIGGER IF EXISTS trg_deduct_stock_sale ON sale_product;
DROP TRIGGER IF EXISTS trg_insert_invoice_products ON sale_product;
DROP TRIGGER IF EXISTS trg_invoice_products_sale ON sale_product;
DROP TRIGGER IF EXISTS trg_log_sale_ledger ON sale_product;
DROP TRIGGER IF EXISTS trg_update_invoice_total ON sale_product;







DO $$ 
DECLARE 
    trigger_record RECORD;
BEGIN
    -- Loop through each trigger and drop it
    FOR trigger_record IN 
        SELECT trigger_name, event_object_table 
        FROM information_schema.triggers 
        WHERE trigger_name IN (
            'trg_update_inventory_add_item',
            'trg_update_inventory_add_product',
            'trg_update_inventory_from_add_product',
            'trigger_update_payment_status',
            'ledger_balance_trigger',
            'trg_update_invoice_payment_status_delete',
            'trg_update_invoice_payment_status_insert',
            'trg_update_invoice_payment_status_update',
            'trg_update_invoice_total_delete',
            'trg_update_invoice_total_insert',
            'trg_update_invoice_total_update'
        )
    LOOP
        -- Execute DROP TRIGGER command dynamically
        EXECUTE FORMAT('DROP TRIGGER IF EXISTS %I ON %I CASCADE;', 
                       trigger_record.trigger_name, trigger_record.event_object_table);
    END LOOP;
END $$;








DROP FUNCTION IF EXISTS update_inventory_on_add_item CASCADE;
DROP FUNCTION IF EXISTS update_inventory_from_add_product CASCADE;
DROP FUNCTION IF EXISTS update_add_product_on_inventory_change CASCADE;
DROP FUNCTION IF EXISTS update_ledger_on_invoice CASCADE;
DROP FUNCTION IF EXISTS update_payment_on_invoice CASCADE;
DROP FUNCTION IF EXISTS update_payment_status CASCADE;