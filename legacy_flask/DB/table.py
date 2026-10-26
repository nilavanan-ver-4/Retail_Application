import psycopg2

def create_tables():
    try:
        # Connect to the PostgreSQL database
        conn = psycopg2.connect(
            dbname="postgres",
            user="postgres",
            password="1234",
            host="localhost",
            port="5432"
        )
        cursor = conn.cursor()

        # Read the SQL file and execute its contents
        with open("table.sql", "r") as file:
            sql_script = file.read()
            cursor.execute(sql_script)
            conn.commit()
            print("Tables created successfully!")

    except Exception as e:
        print(f"An error occurred: {e}")

    finally:
        # Close the cursor and connection
        if cursor:
            cursor.close()
        if conn:
            conn.close()

if __name__ == "__main__":
    create_tables()
