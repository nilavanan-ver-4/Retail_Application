    SELECT trigger_name, event_manipulation, event_object_table 
    FROM information_schema.triggers 
    ORDER BY event_object_table, trigger_name;



SELECT proname, proargtypes, prorettype 
FROM pg_proc 
JOIN pg_namespace ON pg_proc.pronamespace = pg_namespace.oid 
WHERE nspname = 'public' -- Change schema name if needed
ORDER BY proname;


SELECT conname, conrelid::regclass, confrelid::regclass, conkey, confkey
FROM pg_constraint
WHERE conrelid = 'sale_product'::regclass;
