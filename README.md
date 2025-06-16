# Retail Application

## Overview
This is a Retail Application built using Flask, PostgreSQL, and Python. It includes features for managing retail operations, with dependencies for database connectivity, PDF generation, and data manipulation.

## Prerequisites
- Python 3.x
- PostgreSQL
- Git

## Setup Instructions

### 1. Clone the Repository
Clone the project from the GitHub repository:
```bash
git clone https://github.com/nilavanan-ver-4/Retail_Application
cd Retail_Application
```

### 2. Install Dependencies
The project dependencies are listed in `requirements.txt`. Install them using pip:
```bash
pip install -r requirements.txt
```
The dependencies include:
- flask
- flask-sqlalchemy
- pyodbc
- urllib3
- psycopg2
- weasyprint
- pandas

### 3. Configure PostgreSQL
- Ensure PostgreSQL is installed and running on your system.
- Create a database for the application.
- Update the database connection settings in `app.py` (or a configuration file) with your PostgreSQL credentials (e.g., database name, user, password).

### 4. Run the Application
Run the Flask application using the following command:
```bash
python app.py
```
The application should now be running on your local server (typically at `http://localhost:5000`).

## Project Structure
- `app.py`: Main application file.
- `DB/`: Folder for database-related files.
- `static/`: Folder for static assets (e.g., CSS, JS, images).
- `templates/`: Folder for HTML templates.
- `uploads/`: Folder for uploaded files.
- `.gitignore`: Git ignore file.
- `LICENSE`: License file.
- `requirements.txt`: List of Python dependencies.

## Additional Notes
- Ensure all dependencies are compatible with your Python version.
- If you encounter issues with `weasyprint`, you may need to install additional system dependencies (e.g., GTK for PDF rendering).

## License
See the `LICENSE` file for more details.
