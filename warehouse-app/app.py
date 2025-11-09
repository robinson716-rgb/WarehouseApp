from flask import Flask, request, jsonify, render_template, session, redirect
import psycopg2
from psycopg2.extras import RealDictCursor
import hashlib
from datetime import datetime

app = Flask(__name__)
app.secret_key = 'your-secret-key-change-me'

# DB from .env
DB_CONFIG = {
    'host': '34.135.249.159',
    'database': 'postgres',
    'user': 'postgres',
    'password': 'postgres',
    'port': 5432,
    'sslmode': 'require'
}

def get_db():
    return psycopg2.connect(**DB_CONFIG, cursor_factory=RealDictCursor)

def verify_login(username, password):
    try:
        conn = get_db()
        cursor = conn.cursor()
        hashed_password = hashlib.sha256(password.encode()).hexdigest()
        cursor.execute('SELECT "Role" FROM "user logins" WHERE "Username" = %s AND "Hashed Password" = %s',
                       (username, hashed_password))
        user = cursor.fetchone()
        cursor.close()
        conn.close()
        if user:
            role = user['Role'] if user['Role'] else "Guest"
            session['username'] = username
            session['role'] = role
            return True
        return False
    except:
        return False

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        if verify_login(username, password):
            return redirect('/')
        return render_template('login.html', error='Invalid credentials')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')

@app.route('/')
def index():
    if 'username' not in session:
        return redirect('/login')
    return render_template('index.html')

@app.route('/scan', methods=['POST'])
def scan():
    if 'username' not in session:
        return jsonify({"error": "Unauthorized"}), 401
    barcode = request.json.get('barcode')
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM locations WHERE barcode = %s", (barcode,))
    location = cursor.fetchone()
    if not location:
        cursor.close()
        conn.close()
        return jsonify({"error": "Location not found"}), 404
    # Get pallet spots for this location
    cursor.execute("""
        SELECT ps.*, b.batch_number, b.best_before_date, b.quantity, b.status, b.price_per_kg,
               i.ingredient_name
        FROM pallet_spots ps
        LEFT JOIN fps_batches b ON ps.batch_id = b.batch_id
        LEFT JOIN fps_ingredients i ON b.ingredient_id = i.ingredient_id
        WHERE ps.location_id = %s
        ORDER BY ps.level
    """, (location['id'],))
    spots = cursor.fetchall()
    cursor.close()
    conn.close()
    return jsonify({
        "location": location,
        "spots": spots
    })

@app.route('/update_spot', methods=['POST'])
def update_spot():
    if 'username' not in session:
        return jsonify({"error": "Unauthorized"}), 401
    data = request.json
    full_code = data['full_code']
    status = data['status']
    ingredient_name = data.get('ingredient_name')  # Optional
    batch_number = data.get('batch_number')
    bbf = data.get('bbf')
    kg = data.get('kg')
    username = session['username']
    conn = get_db()
    cursor = conn.cursor()
    # Get pallet spot
    cursor.execute("SELECT id, location_id FROM pallet_spots WHERE full_code = %s", (full_code,))
    spot = cursor.fetchone()
    if not spot:
        # Create new spot if not exists
        location_barcode = full_code[:-2]  # e.g. HH01-B -> HH01
        level = full_code[-1]
        cursor.execute("SELECT id FROM locations WHERE barcode = %s", (location_barcode,))
        loc = cursor.fetchone()
        if loc:
            cursor.execute("""
                INSERT INTO pallet_spots (location_id, level)
                VALUES (%s, %s) RETURNING id
            """, (loc['id'], level))
            spot_id = cursor.fetchone()['id']
            spot = {'id': spot_id, 'location_id': loc['id']}
    # Find or create ingredient
    ingredient_id = None
    if ingredient_name:
        cursor.execute("SELECT ingredient_id FROM fps_ingredients WHERE ingredient_name = %s", (ingredient_name,))
        ing = cursor.fetchone()
        if ing:
            ingredient_id = ing['ingredient_id']
        else:
            cursor.execute("""
                INSERT INTO fps_ingredients (ingredient_name)
                VALUES (%s) RETURNING ingredient_id
            """, (ingredient_name,))
            ingredient_id = cursor.fetchone()['ingredient_id']
    # Find or create batch
    batch_id = None
    if batch_number and kg is not None:
        cursor.execute("""
            SELECT batch_id FROM fps_batches
            WHERE ingredient_id = %s AND batch_number = %s
        """, (ingredient_id, batch_number))
        batch = cursor.fetchone()
        if batch:
            batch_id = batch['batch_id']
        else:
            cursor.execute("""
                INSERT INTO fps_batches (ingredient_id, batch_number, best_before_date, quantity, status)
                VALUES (%s, %s, %s, %s, %s) RETURNING batch_id
            """, (ingredient_id, batch_number, bbf, kg, status))
            batch_id = cursor.fetchone()['batch_id']
    # Update pallet spot
    cursor.execute("""
        UPDATE pallet_spots
        SET batch_id = %s, updated_by = %s, updated_at = NOW()
        WHERE id = %s RETURNING *
    """, (batch_id, username, spot['id']))
    result = cursor.fetchone()
    conn.commit()
    cursor.close()
    conn.close()
    return jsonify(result)

if __name__ == '__main__':
    app.run(debug=True)
