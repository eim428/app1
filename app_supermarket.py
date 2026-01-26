import streamlit as st
import pandas as pd
import sqlite3
import random
import os
import math
import json
from fpdf import FPDF
import io
import altair as alt
import base64
import openpyxl
import hashlib
import plotly.express as px
import streamlit_highcharts as hg
from reportlab.pdfgen import canvas
from datetime import datetime, timedelta
from reportlab.lib.pagesizes import letter
import streamlit.components.v1 as components


  


#if os.path.exists('dblite/supermarket.db'):
#    os.remove('dblite/supermarket.db')

# --- 1. CORE ENGINE ---
def get_connection():
    return sqlite3.connect('dblite/supermarket.db', check_same_thread=False)

def hash_password(password):
    # Mengubah password menjadi hash SHA-256
    return hashlib.sha256(str.encode(password)).hexdigest()

# Contoh cara memasukkan user baru ke database (jalankan sekali saja)
def add_user(username, password, role):
    conn = get_connection()
    cursor = conn.cursor()
    hashed_p = hash_password(password)
    cursor.execute("INSERT INTO users (username, password, role) VALUES (?, ?, ?)", 
                   (username, hashed_p, role))
    conn.commit()
    conn.close()


# --- 2. PREDICTION LOGIC ---
def get_stock_predictions():
    conn = get_connection()
    # Ambil data penjualan 30 hari terakhir
    date_limit = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
    query = """
        SELECT product_name, SUM(qty) as total_sold 
        FROM trans_detail 
        JOIN trans_master ON trans_detail.trans_id = trans_master.id
        WHERE trans_master.date >= ?
        GROUP BY product_name
    """
    df_sales = pd.read_sql(query, conn, params=[date_limit])
    df_products = pd.read_sql("SELECT name, stock FROM products", conn)
    conn.close()

    # Gabungkan data
    df_pred = pd.merge(df_products, df_sales, left_on='name', right_on='product_name', how='left').fillna(0)
    
    # Hitung Kecepatan Penjualan (Daily Velocity)
    df_pred['daily_velocity'] = df_pred['total_sold'] / 30
    
    # Hitung Sisa Hari (ETA Out of Stock)
    def calculate_eta(row):
        if row['daily_velocity'] <= 0: return 999 # Stok aman/tidak laku
        return math.floor(row['stock'] / row['daily_velocity'])

    df_pred['days_left'] = df_pred.apply(calculate_eta, axis=1)
    return df_pred

def show_forecasting():
    st.markdown("<h2 style='color:var(--primary); font-family:Orbitron;'>🔮 INVENTORY PREDICTION</h2>", unsafe_allow_html=True)

    check_low_stock_alerts(threshold=5)    
    
    df_pred = get_stock_predictions()
    
    # Filter Barang Kritikal (Habis < 7 hari)
    critical = df_pred[df_pred['days_left'] <= 7].sort_values('days_left')
    
    if not critical.empty:
        st.markdown('<div class="glass-card">', unsafe_allow_html=True)
        st.subheader("🚨 Restock Priority (ETA < 7 Days)")
        for _, row in critical.iterrows():
            st.markdown(f"""
                <div style="display:flex; justify-content:space-between; border-bottom:1px solid rgba(255,255,255,0.1); padding:10px 0;">
                    <span>{row['name']}</span>
                    <span class="critical-alert">Habis dalam {row['days_left']} hari</span>
                </div>
            """, unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)

    # Visualisasi Proyeksi Stok
    st.markdown('<div class="glass-card">', unsafe_allow_html=True)
    st.subheader("Stock Endurance Analysis")
    fig = px.bar(df_pred, x='name', y='days_left', color='days_left',
                 labels={'days_left': 'Sisa Hari (Estimasi)', 'name': 'Produk'},
                 color_continuous_scale='RdYlGn', template="plotly_dark")
    fig.add_hline(y=7, line_dash="dash", line_color="red", annotation_text="Danger Zone")
    fig.update_layout(paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)')
    st.plotly_chart(fig, width='stretch')
    st.markdown('</div>', unsafe_allow_html=True)

def generate_po_pdf(supplier_name, items):
    buffer = io.BytesIO()
    p = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter

    # Header
    p.setFont("Helvetica-Bold", 16)
    p.drawString(50, height - 50, "PURCHASE ORDER - FUTURE MART 2026")
    p.setFont("Helvetica", 10)
    p.drawString(50, height - 70, f"Tanggal: {datetime.now().strftime('%d %B %Y')}")
    p.drawString(50, height - 85, f"Kepada Yth: {supplier_name}")
    
    # Table Header
    p.line(50, height - 110, 550, height - 110)
    p.drawString(60, height - 125, "Nama Produk")
    p.drawString(400, height - 125, "Estimasi Qty Order")
    p.line(50, height - 135, 550, height - 135)

    # Table Content
    y = height - 155
    for item in items:
        p.drawString(60, y, item['name'])
        p.drawString(400, y, f"{item['order_qty']} Unit")
        y -= 20

    # Footer
    p.line(50, 150, 550, 150)
    p.drawString(50, 130, "Catatan: Mohon konfirmasi ketersediaan stok segera.")
    p.drawString(450, 80, "Manager Operasional,")
    p.drawString(450, 40, "_________________")
    
    p.showPage()
    p.save()
    buffer.seek(0)
    return buffer

# --- 3. UI MODULES ---
def show_supplier_po():
    st.markdown("<h2 style='color:var(--primary); font-family:Orbitron;'>🏭 SUPPLIER & AUTO-PO</h2>", unsafe_allow_html=True)
    conn = get_connection()
    
    # Ambil data prediksi stok (dari fungsi sebelumnya)
    # Asumsikan kita butuh restock barang yang sisa harinya < 7 hari
    #from main_code import get_stock_predictions # Simulasi pemanggilan fungsi pred
    df_pred = get_stock_predictions()
    critical_items = df_pred[df_pred['days_left'] <= 7]

    st.markdown('<div class="glass-card">', unsafe_allow_html=True)
    st.subheader("Otomasi Pesanan Barang")
    
    if not critical_items.empty:
        sel_supp = st.selectbox("Pilih Supplier Tujuan", pd.read_sql("SELECT name FROM suppliers", conn))
        
        # Tabel barang yang akan di-PO
        order_list = []
        for _, row in critical_items.iterrows():
            order_qty = 50  # Default restock qty
            order_list.append({'name': row['name'], 'order_qty': order_qty})
        
        st.write("Daftar Barang yang direkomendasikan untuk dipesan:")
        st.table(pd.DataFrame(order_list))
        
        # Tombol Generate PDF
        pdf_file = generate_po_pdf(sel_supp, order_list)
        st.download_button(
            label="📄 GENERATE PURCHASE ORDER (PDF)",
            data=pdf_file,
            file_name=f"PO_{sel_supp}_{datetime.now().strftime('%Y%m%d')}.pdf",
            mime="application/pdf",
            width='stretch'
        )
    else:
        st.success("Stok masih aman. Belum ada kebutuhan Purchase Order.")
    st.markdown('</div>', unsafe_allow_html=True)


def hex_to_rgba(hex_str, opacity=0.3):
    hex_str = hex_str.lstrip('#')
    r, g, b = tuple(int(hex_str[i:i+2], 16) for i in (0, 2, 4))
    return f'rgba({r}, {g}, {b}, {opacity})'


def apply_theme():
    # Hitung warna teks adaptif

    if 'theme' not in st.session_state: return
    t = st.session_state.get('theme', {
        'bg_color': '#0E1117',
        'top_bar': '#95A5A6',
        'primary_color': '#00FFA3',
        'font_family': 'Segoe UI',
        'font_size': '14px'
    })
    sidebar_text = get_contrast_color(t['bg_color'])
    body_text = get_contrast_color(t['body_color'])
    btn_text_hover = get_contrast_color(t['bg_color'])

    
    st.markdown(f"""
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Orbitron:wght@400;700&family=Roboto:wght@300;400;700&display=swap');
        


        /* 1. TOP BAR (HEADER) */
        header[data-testid="stHeader"] {{
            background-color: {t['top_bar']} !important; /* Disamakan dengan body atau beri warna lain */            
            /*border-bottom: 1px solid rgba(255, 255, 255, 0.1);*/
        }}

        /* 1. WARNA HALAMAN UTAMA (BODY) */
        .stApp {{
            background-color: {t['body_color']} !important;
            color: white;
            font-family: '{t['font_family']}', sans-serif;
        }}

        /* 2. WARNA SIDEBAR (DIBEDAKAN) */
        [data-testid="stSidebar"] {{
            background-color: {t['bg_color']} !important;
            border-right: 1px solid rgba(255, 255, 255, 0.1);
        }}

        /* Futuristic Card Effect */
        div[data-testid="stMetricValue"] {{
            color: var(--primary);
            text-shadow: 0 0 5px var(--primary);
        }}

        /* --- 1. KOTAK FORM LOGIN (NEON BORDER) --- */
        [data-testid="stForm"] {{
            border: 2px solid {t['bg_color']} !important;
            border-radius: 5px !important;
            padding: 30px !important;
            background-color: rgba(255, 255, 255, 0.03) !important; /* Efek Glassmorphism */
            box-shadow: 0 0 10px rgba({hex_to_rgba(t['bg_color'])}, 0.3) !important;
            backdrop-filter: blur(5px); /* Efek Blur di belakang kotak */
        }}

        /* --- 2. INPUT FIELD DALAM FORM --- */
        .stTextInput input {{
            background-color: rgba(0, 0, 0, 0.2) !important;
            color: white !important;
            border: 1px solid rgba({hex_to_rgba(t['bg_color'])}, 0.3) !important;
            border-radius: 8px !important;
        }}
        
        /* TARGET SEMUA TOMBOL */
        div.stButton > button, 
        div.stDownloadButton > button, 
        button[kind="secondaryFormSubmit"] {{
            /* BACKGROUND HARUS GELAP/TRANSPARAN AGAR TEKS TERANG TERLIHAT */
            background-color: rgba({hex_to_rgba(t['bg_color'])}, 0.3) !important; 
            color: {t['primary_color']} !important;
            
            border: 2px solid {t['primary_color']} !important;
            border-radius: 8px !important;
            padding: 10px !important;
            font-weight: 700 !important;
            font-family: 'Orbitron', sans-serif !important;
            text-transform: uppercase;
            
            /* Efek bayangan agar teks lebih 'tebal' */
            text-shadow: 0 0 5px rgba(0,0,0,0.5);
            transition: all 0.3s ease;
        }}

        /* SAAT KURSOR DI ATAS TOMBOL */
        div.stButton > button:hover, 
        div.stDownloadButton > button:hover, 
        button[kind="secondaryFormSubmit"]:hover {{
            background-color: {t['primary_color']} !important;
            color: {btn_text_hover} !important; /* Otomatis Hitam jika primary cerah */
            box-shadow: 0 0 20px {t['primary_color']} !important;
        }}

        /* MENGHILANGKAN BACKGROUND PUTIH BAWAAN ANDROID/MOBILE */
        div.stButton > button:active, div.stButton > button:focus {{
            background-color: #0a101f !important;
            color: {t['primary_color']} !important;
        }}

        
        
       /* Semua teks di dalam sidebar (label, radio, p) mengikuti kontras */
        [data-testid="stSidebar"] .stMarkdown, 
        [data-testid="stSidebar"] label, 
        [data-testid="stSidebar"] p,
        [data-testid="stSidebar"] .stRadio label {{
            color: {sidebar_text} !important;
        }}

        /* 3. INPUT FIELD (Agar tetap terbaca) */
        .stTextInput input, .stSelectbox select {{
            background-color: rgba(255,255,255,0.1) !important;
            color: {body_text} !important;
        }}

        /* 4. FIX ICON PANAH (Agar tidak muncul teks) */
        [data-testid="stIcon"], .st-emotion-cache-6qob1r, [data-testid="collapsedControl"] {{
            font-family: serif !important;
            color: {sidebar_text} !important;
        }}

             
        </style>
    """, unsafe_allow_html=True)



  
# --- 3. PROMO LOGIC ---
def calculate_promos(cart):
    total_before = sum(item['Subtotal'] for item in cart)
    discount_total = 0
    promo_details = []

    for item in cart:
        # Contoh Promo: Buy 2 Get 1 Free untuk Susu Steril
        if item['Product'] == 'Susu Steril' and item['Qty'] >= 3:
            free_units = item['Qty'] // 3
            discount_val = free_units * item['Price']
            discount_total += discount_val
            promo_details.append(f"Promo B2G1 Susu: -{discount_val:,.0f}")

    # Contoh Promo: Diskon Global 5% jika belanja > 200rb
    if total_before >= 200000:
        global_disc = total_before * 0.05
        discount_total += global_disc
        promo_details.append(f"Diskon Member 5%: -{global_disc:,.0f}")

    return discount_total, promo_details

# --- 4. UI MODULES ---
# --- 3. HELPERS ---
def create_receipt_text(t_id, date, cashier, items, total):
    receipt = f"======================\n       FUTURE MART 2026         \n   NEO-RETAIL POS SYSTEM        \n======================\nNo. Inv : {t_id}\nTanggal : {date}\nKasir   : {cashier}\n----------------------\n"
    for i in items:
        receipt += f"{i['Product'][:20]:<20}\n  {i['Qty']} x {i['Price']:,.0f}      {i['Subtotal']:>10,.0f}\n"
    receipt += "--------------------------------\n"
    receipt += f"TOTAL           Rp {total:>10,.0f}\n======================\n  TERIMA KASIH TELAH BERBELANJA \n      POWERED BY KENYED      \n"
    return receipt
    
# --- 3. UI MODULES ---

def show_accounting():
    st.markdown("<h1 style='color:var(--primary); font-family:Orbitron;'>FINANCIAL & AUDIT</h1>", unsafe_allow_html=True)
    conn = get_connection()
    df_m = pd.read_sql("SELECT * FROM trans_master", conn)
    
    # --- TAB LAPORAN ---
    tab1, tab2 = st.tabs(["📊 Jurnal Umum", "👥 Performa Kasir"])
    
    with tab1:
        st.markdown('<div class="glass-card">', unsafe_allow_html=True)
        st.subheader("Data Transaksi")
        st.dataframe(df_m.sort_values('date', ascending=False), width='stretch')
        st.markdown('</div>', unsafe_allow_html=True)

    with tab2:
        st.subheader("Analisis Produktivitas Karyawan")
        # Agregasi data per Kasir
        cashier_perf = df_m.groupby('cashier').agg({
            'id': 'count',
            'total': 'sum'
        }).rename(columns={'id': 'Total Transaksi', 'total': 'Total Omzet'}).reset_index()
        
        c1, c2 = st.columns([1, 1.5])
        with c1:
            st.markdown('<div class="glass-card">', unsafe_allow_html=True)
            st.write("Ranking Penjualan")
            st.table(cashier_perf.sort_values('Total Omzet', ascending=False))
            st.markdown('</div>', unsafe_allow_html=True)
        
        with c2:
            st.markdown('<div class="glass-card">', unsafe_allow_html=True)
            fig = px.bar(cashier_perf, x='cashier', y='Total Omzet', 
                         color='Total Transaksi', title="Perbandingan Omzet Kasir",
                         template="plotly_dark", color_continuous_scale='Viridis')
            fig.update_layout(paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)')
            st.plotly_chart(fig, width='stretch')
            st.markdown('</div>', unsafe_allow_html=True)


#Auto Backup Cloud
def show_database_tools():
    st.markdown("<h2 style='color:var(--primary); font-family:Orbitron;'>🛡️ DATA PROTECTION</h2>", unsafe_allow_html=True)
    st.markdown('<div class="glass-card">', unsafe_allow_html=True)
    
    st.subheader("Manual Cloud Backup")
    st.write("Unduh salinan database Anda secara berkala untuk disimpan di Google Drive atau Hardisk Eksternal.")
    
    # Membaca file database SQLite
    with open("dblite/supermarket.db", "rb") as f:
        db_bytes = f.read()
    
    st.download_button(
        label="📥 DOWNLOAD DATABASE (.db)",
        data=db_bytes,
        file_name=f"FutureMart_Backup_{datetime.now().strftime('%Y%m%d_%H%M')}.db",
        mime="application/octet-stream",
        width='stretch'
    )
    
    st.markdown("---")
    st.subheader("Data Cleanup")
    st.warning("Hanya gunakan fitur ini saat pergantian tahun fiskal atau jika database terlalu berat.")
    
    if st.button("🗑️ RESET TRANSACTION HISTORY", help="Menghapus semua riwayat transaksi tapi menyimpan data produk"):
        conn = get_connection()
        c = conn.cursor()
        c.execute("DELETE FROM trans_detail")
        c.execute("DELETE FROM trans_master")

        conn.commit()
        st.success("Riwayat transaksi telah dibersihkan!")
    
    st.markdown('</div>', unsafe_allow_html=True)

def show_inventory():
    st.markdown("<h2 style='color:var(--primary); font-family:Orbitron;'>📦 INVENTORY & BULK IMPORT</h2>", unsafe_allow_html=True)
    conn = get_connection()
    
    tab1, tab2, tab3 = st.tabs(["📊 Daftar Stok", "➕ Tambah Manual", "📂 Bulk Upload Excel"])
    
    # --- TAB 1: DAFTAR STOK ---
    with tab1:
        st.markdown('<div class="glass-card">', unsafe_allow_html=True)
        df_p = pd.read_sql("SELECT * FROM products", conn)
        edited_df = st.data_editor(df_p, num_rows="dynamic", width='stretch', key="inv_editor")
        
        if st.button("💾 SIMPAN SEMUA PERUBAHAN"):
            c = conn.cursor()
            for index, row in edited_df.iterrows():
                c.execute("""UPDATE products SET barcode=?, name=?, category=?, price=?, stock=?, cost_price=? WHERE id=?""", 
                          (row['barcode'], row['name'], row['category'], row['price'], row['stock'], row['cost_price'], row['id']))
            conn.commit()
            st.success("Database Diperbarui!")
        st.markdown('</div>', unsafe_allow_html=True)

    # --- TAB 2: TAMBAH MANUAL (Seperti Sebelumnya) ---
    with tab2:
        # ... (Kode form input manual Anda) ...
        pass

    # --- TAB 3: BULK UPLOAD EXCEL ---
    with tab3:
        st.markdown('<div class="glass-card">', unsafe_allow_html=True)
        st.subheader("Impor Produk dari Excel/CSV")
        st.write("Pastikan file Anda memiliki kolom: **barcode, name, category, price, stock, cost_price**")
        
        # Template download untuk user
        template = pd.DataFrame(columns=['barcode', 'name', 'category', 'price', 'stock', 'cost_price'])
        st.download_button("📥 Download Template Excel", 
                           data=template.to_csv(index=False), 
                           file_name="template_produk.csv", mime="text/csv")
        
        uploaded_file = st.file_uploader("Pilih file Excel atau CSV", type=['xlsx', 'csv'])
        
        if uploaded_file is not None:
            try:
                if uploaded_file.name.endswith('.csv'):
                    df_upload = pd.read_csv(uploaded_file)
                else:
                    df_upload = pd.read_excel(uploaded_file)
                
                st.write("Preview Data yang akan diimpor:")
                st.dataframe(df_upload.head(), width='stretch')
                
                if st.button("🚀 KONFIRMASI IMPOR DATA"):
                    c = conn.cursor()
                    for _, row in df_upload.iterrows():
                        c.execute("""INSERT INTO products (barcode, name, category, price, stock, cost_price) 
                                     VALUES (?,?,?,?,?)""", 
                                  (str(row['barcode']), row['name'], row['category'], row['price'], row['stock'],row['cost_price']))
                    conn.commit()
                    st.success(f"Berhasil mengimpor {len(df_upload)} produk!")
                    st.rerun()
            except Exception as e:
                st.error(f"Terjadi kesalahan: {e}")
        st.markdown('</div>', unsafe_allow_html=True)

def graph_packedbubble():
    if 'theme' not in st.session_state: return
    t = st.session_state.get('theme', {
        'bg_color': '#0E1117',
        'top_bar': '#95A5A6',
        'primary_color': '#00FFA3',
        'font_family': 'Segoe UI',
        'font_size': '14px'
    })
    sidebar_text = get_contrast_color(t['bg_color'])
    body_text = get_contrast_color(t['body_color'])
    btn_text_hover = get_contrast_color(t['bg_color'])
    warna_bg = get_contrast_color(t['top_bar'])

    conn = get_connection()    
    #zx = pd.read_sql("select product_name, category,sum(qty) as jumlah from trans_detail group by product_name,category", conn)
    query = "select category,product_name,sum(qty) as jumlah from trans_detail group by category, product_name"
    zx = pd.read_sql(query, conn)

    #result= query.fetch_veh_graph()
    df=pd.DataFrame(zx, columns=['product_name','category','jumlah'])
    # 2. Transform DataFrame into Highcharts series format
    chart_data = []
    for category in df['category'].unique():
        subset = df[df['category'] == category]
        data_points = subset[['category','product_name', 'jumlah']].rename(columns={'category': 'category','product_name': 'name', 'jumlah': 'value'}).to_dict('records')
        chart_data.append({'name': category, 'data': data_points})
        #data_points = subset[['MAKE', 'JUMLAH']].rename(columns={'MAKE': 'make', 'JUMLAH': 'jumlah'}).to_dict('records')
        #chart_data = [{'product_name': point['product_name'],'name': point['name'], 'value': point['value']} for point in data_points]
        
    # 3. Define Chart Configuration
    chartDef = { 'chart': { 'height': '60%',
                'type': 'packedbubble',
                'backgroundColor': t['top_bar']},
    'plotOptions': { 'packedbubble': { 'dataLabels': { 'enabled': True,
                                                        #'filter': { 'operator': '>',
                                                        #            'property': 'y'
                                                        #            'value': 10},
                                                        'format': '{point.name}',
                                                        'style': { 'color': 'black',
                                                                    'fontWeight': 'normal',
                                                                    'textOutline': 'none',
                                                                    'backgroundColor':t['top_bar']}},
                                        'layoutAlgorithm': { 'dragBetweenSeries': True,
                                                            'gravitationalConstant': 0.08,
                                                            'parentNodeLimit': True,
                                                            'seriesInteraction': False,
                                                            'splitSeries': True},
                                        'maxSize': '100%',
                                        'minSize': '20%',
                                        'zMax': 1000,
                                        'zMin': 0}},
    'series': chart_data
    }            
    data=hg.streamlit_highcharts(chartDef,640)
    st.markdown("##")
    return data  

def graph_bar():
    
    if 'theme' not in st.session_state: return
    t = st.session_state.get('theme', {
        'bg_color': '#0E1117',
        'top_bar': '#95A5A6',
        'primary_color': '#00FFA3',
        'font_family': 'Segoe UI',
        'font_size': '14px'
    })
    sidebar_text = get_contrast_color(t['bg_color'])
    body_text = get_contrast_color(t['body_color'])
    btn_text_hover = get_contrast_color(t['bg_color'])
    warna_bg = get_contrast_color(t['top_bar'])

    conn = get_connection()    

    query = "select product_name,sum(qty) as jumlah from trans_detail group by product_name"
    zx = pd.read_sql(query, conn)


    st.subheader("Category by Quantity")
    source = pd.DataFrame({
    "Quantity ($)": zx["jumlah"],
    "Product Name": zx["product_name"]
    })

    bar_chart = alt.Chart(source).mark_bar().encode(
    x="sum(Quantity ($)):Q",
    y=alt.Y("Product Name:N", sort="-x")

    )
    st.altair_chart(bar_chart, width='stretch') #,theme=theme_plotly,)

def show_dashboard():
    st.markdown(f"<h1 style='color:var(--primary); font-family:Orbitron;'>CORE DASHBOARD</h1>", unsafe_allow_html=True)
    t = st.session_state.theme
    conn = get_connection()    

    if st.session_state.user_role == 'Admin':

        coll1,coll2=st.columns(2)

        with coll1:
            st.subheader("Product and Make by Quantity")
            graph_packedbubble()
            
        with coll2:
            graph_bar()
    st.markdown("##")

    

def barcode_scanner():
    # Komponen HTML/JS untuk akses kamera
    scanner_code = """
    <div id="interactive" class="viewport" style="width: 100%; height: 300px; position: relative;">
        <video id="video" style="width: 100%; height: 100%; object-fit: cover;"></video>
        <canvas class="drawingBuffer" style="display: none;"></canvas>
    </div>
    <script src="https://cdn.jsdelivr.net/npm/@ericblade/quagga2/dist/quagga.min.js"></script>
    <script>
        Quagga.init({
            inputStream: {
                name: "Live",
                type: "LiveStream",
                target: document.querySelector('#interactive'),
                constraints: { facingMode: "environment" } // Gunakan kamera belakang
            },
            decoder: {
                readers: ["ean_reader", "code_128_reader", "ean_8_reader"]
            }
        }, function(err) {
            if (err) { console.log(err); return; }
            Quagga.start();
        });

        Quagga.onDetected(function(result) {
            var code = result.codeResult.code;
            // Kirim kode barcode kembali ke Python Streamlit
            window.parent.postMessage({
                type: 'streamlit:setComponentValue',
                value: code
            }, '*');
            Quagga.stop();
        });
    </script>
    """
    # Menangkap hasil scan dari JavaScript
    st.markdown("### 📷 Scan Barcode")
    scan_result = components.html(scanner_code, height=350)
    return scan_result

# Gunakan di menu POS
def show_pos_with_scanner():
    st.markdown("<h6 style='color:var(--primary); font-family:Orbitron;'>🛒 Kamera</h6>", unsafe_allow_html=True)
    
    # Toggle untuk membuka kamera
    if st.checkbox("Buka Scanner Kamera"):
        barcode = barcode_scanner()
        if barcode:
            st.success(f"Barcode Terdeteksi: {barcode}")
            # Cari produk di database berdasarkan barcode
            # query = "SELECT * FROM products WHERE barcode = ?"
    
    # Sisanya adalah logika transaksi seperti sebelumnya

#CETAK PRINTER TERMAL 58mm atau 80mm
def print_receipt_thermal(receipt_text):
    # Menggunakan iframe tersembunyi untuk mencetak agar tidak merusak tampilan utama
    receipt_html = f"""
    <html>
    <body onload="window.print();">
        <div id="receipt" style="font-family: 'Courier New', Courier, monospace; width: 200px; font-size: 12px;">
            <pre style="white-space: pre-wrap; margin: 0;">{receipt_text}</pre>
        </div>
        <script>
            // Event ini dipicu setelah dialog print ditutup (di-print atau di-cancel)
            window.onafterprint = function() {{
                // Mengirim pesan ke Streamlit bahwa proses selesai (opsional)
                console.log("Print selesai/ditutup");
            }};
        </script>
    </body>
    </html>
    """
    # Gunakan height sedikit lebih besar agar tidak ada scrollbar di dalam iframe cetak
    components.html(receipt_html, height=80)

def show_pos():
    st.markdown("<h2 style='color:var(--primary); font-family:Orbitron;'>🛒 SMART TERMINAL</h2>", unsafe_allow_html=True)
    st.markdown('<div class="glass-card">', unsafe_allow_html=True)
    c1, c2, c3 = st.columns([2, 1, 1])
    t_id = c1.text_input("Invoice", f"INV-{datetime.now().strftime('%y%m%d%H%M%S')}", disabled=True)
    t_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    t_cashier = c3.text_input("Cashier", st.session_state.username)
    st.markdown('</div>', unsafe_allow_html=True)
    
    conn = get_connection()    
    # --- MEMBER IDENTIFICATION ---
    show_pos_with_scanner()
    st.markdown('<div class="glass-card">', unsafe_allow_html=True)
    m_search = st.text_input("Input No. Member (Kosongkan jika Non-Member)", placeholder="Contoh: 0812345678")
    member_data = None
    if m_search:
        res = pd.read_sql("SELECT * FROM members WHERE phone=?", conn, params=[m_search])
        if not res.empty:
            member_data = res.iloc[0]
            st.markdown(f'<span class="member-badge">⭐ MEMBER: {member_data["name"]} | Poin: {member_data["points"]}</span>', unsafe_allow_html=True)
        else:
            st.warning("Member tidak ditemukan")
    st.markdown('</div>', unsafe_allow_html=True)

    # --- TRANSACTION ---
    df_p = pd.read_sql("SELECT * FROM products WHERE stock > 0", conn)
    st.markdown('<div class="glass-card">', unsafe_allow_html=True)
    col1, col2, col3 = st.columns([3, 1, 1])
    sel_p = col1.selectbox("Cari Produk", df_p['name'])
    p_data = df_p[df_p['name'] == sel_p].iloc[0]
    col2.write(f"Price: **{p_data['price']:,.0f}**")    
    p_info = df_p[df_p['name'] == sel_p].iloc[0]
    qty = col3.number_input("Qty", min_value=1, max_value=int(p_info['stock']), value=1)
    if col3.button("➕ TAMBAH"):
        st.session_state.cart.append({'Product': sel_p, 'Category': p_data['category'], 'Qty': qty, 'Price': p_info['price'], 'Subtotal': qty * p_info['price']})
    st.markdown('</div>', unsafe_allow_html=True)

    if st.session_state.cart:
        st.markdown('<div class="glass-card">', unsafe_allow_html=True)
        df_cart = pd.DataFrame(st.session_state.cart)
        st.table(df_cart)
        
        subtotal = sum(i['Subtotal'] for i in st.session_state.cart)
        final_discount = 0
        
        # Pilihan Tukar Poin
        if member_data and member_data['points'] > 0:
            use_points = st.checkbox(f"Gunakan Poin sebagai Diskon? (Maks: {member_data['points']})")
            if use_points:
                final_discount = member_data['points']
                st.info(f"Diskon Poin Applied: -Rp {final_discount:,.0f}")

        grand_total = subtotal - final_discount
        st.markdown(f"### GRAND TOTAL: Rp {grand_total:,.0f}")
        
        if st.button("KONFIRMASI PEMBAYARAN", width='stretch'):
            c = conn.cursor()
            t_id = f"TRX-{datetime.now().strftime('%y%m%d%H%M%S')}"
            
            # 1. Simpan Transaksi
            c.execute("INSERT INTO trans_master VALUES (?,?,?,?,?,?)", 
                      (t_id, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), grand_total, st.session_state.username, final_discount, m_search))

            # 2. Update Stok & Hitung Poin Baru (1% dari belanja)
            new_points_earned = int(grand_total * 0.01)
            for i in st.session_state.cart:
                c.execute("UPDATE products SET stock = stock - ? WHERE name = ?", (i['Qty'], i['Product']))
                c.execute("INSERT INTO trans_detail VALUES (?,?,?,?,?,?,?)", (t_id, i['Product'], i['Category'], i['Qty'], i['Price'], i['Subtotal'], 0))
            # 3. Update Poin Member
            if member_data is not None:
                point_change = new_points_earned - (final_discount if use_points else 0)
                c.execute("UPDATE members SET points = points + ? WHERE phone = ?", (point_change, m_search))
            
            conn.commit()
            st.session_state.last_receipt = create_receipt_text(t_id, t_date, t_cashier, st.session_state.cart, subtotal)
            st.session_state.cart = []
            st.success(f"Berhasil! Member mendapatkan +{new_points_earned} poin baru.")
            st.rerun()
    if 'last_receipt' in st.session_state and st.session_state.last_receipt:
        st.markdown('<div class="glass-card">', unsafe_allow_html=True)
        st.subheader("Receipt Preview")
        st.text(st.session_state.last_receipt)
    
        c1, c2, c3 = st.columns(3)
        with c1:
            if st.button("🖨️ CETAK STRUK FISIK"):
                if 'last_receipt' in st.session_state:
                    print_receipt_thermal(st.session_state.last_receipt)
        with c2:
            st.download_button("📥 DOWNLOAD RECEIPT", data=st.session_state.last_receipt, file_name=f"Rec_{t_id}.txt")
        
        with c3:
            if st.button("CLOSE"): st.session_state.last_receipt = None; st.rerun()
            st.markdown('</div>', unsafe_allow_html=True)

def get_all_users():
    conn = get_connection()
    df = pd.read_sql("SELECT username, role FROM users", conn)
    conn.close()
    return df

    
def add_new_user(username, password, role):
    conn = get_connection()
    cursor = conn.cursor()
    try:
        hashed_p = hash_password(password)
        cursor.execute("INSERT INTO users (username, password, role) VALUES (?, ?, ?)", 
                       (username, hashed_p, role))
        conn.commit()
        return True, "User berhasil ditambahkan!"
    except Exception as e:
        return False, f"Error: {e}"
    finally:
        conn.close()

def delete_user(username):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM users WHERE username = ?", (username,))
    conn.commit()
    conn.close()

# Fungsi untuk mencatat aktivitas
def add_log(username, action):
    conn = get_connection()
    cursor = conn.cursor()
    # Menggunakan waktu lokal Indonesia
    cursor.execute("INSERT INTO user_logs (username, action, timestamp) VALUES (?, ?, datetime('now', 'localtime'))", 
                   (username, action))
    conn.commit()
    conn.close()

_="""
def show_user_mgmt():
    st.markdown("<h2 style='color:var(--primary); font-family:Orbitron;'>👥 USER MANAGEMENT</h2>", unsafe_allow_html=True)
    conn = get_connection()
    df_u = pd.read_sql("SELECT username, role FROM users", conn)
    
    st.markdown('<div class="glass-card">', unsafe_allow_html=True)
    st.write("Existing Users")
    st.dataframe(df_u, width='stretch')
    
    with st.expander("Add New User"):
        new_u = st.text_input("New Username")
        new_p = st.text_input("New Password", type="password")
        new_r = st.selectbox("Role", ["Admin", "Cashier"])
        if st.button("Create User"):               
            add_user(new_u, new_p, new_r)
            st.success("User Created!")
            st.success("User Created!")
            st.rerun()            
           
    st.markdown('</div>', unsafe_allow_html=True)
"""

def show_activity_logs():
    t = st.session_state.theme
    st.markdown(f"<h2 style='color:{t['primary_color']}; font-family:Orbitron;'>🖥️ SYSTEM ACTIVITY LOG</h2>", unsafe_allow_html=True)
    
    conn = get_connection()
    # Ambil 50 aktivitas terbaru
    query = "SELECT username as USER, action as AKTIVITAS, timestamp as WAKTU FROM user_logs ORDER BY id DESC LIMIT 50"
    df_logs = pd.read_sql(query, conn)
    conn.close()

    if not df_logs.empty:
        # Menampilkan log dengan styling neon
        st.dataframe(df_logs, width='stretch')
        
        if st.button("🗑️ BERSIHKAN LOG LAMA"):
            # Opsi untuk menghapus log jika sudah terlalu penuh
            conn = sqlite3.connect("inventory.db")
            conn.execute("DELETE FROM activity_logs")
            conn.commit()
            conn.close()
            st.rerun()
    else:
        st.info("Belum ada aktivitas yang tercatat.")


def show_user_mgmt():
    t = st.session_state.theme
    st.markdown(f"<h2 style='color:{t['primary_color']}; font-family:Orbitron;'>👥 USER MANAGEMENT</h2>", unsafe_allow_html=True)
    
    # Bagian 1: List User Terdaftar
    st.subheader("Daftar Akun")
    users_df = get_all_users()
    st.dataframe(users_df, width='stretch')
    
    # Bagian 2: Tambah User Baru
    with st.expander("➕ TAMBAH USER BARU"):
        with st.form("form_add_user", clear_on_submit=True):
            new_u = st.text_input("Username Baru")
            new_p = st.text_input("Password", type="password")
            new_r = st.selectbox("Role", ["Admin", "Cashier"])
            
            if st.form_submit_button("SIMPAN USER"):
                if new_u and new_p:
                    success, msg = add_new_user(new_u, new_p, new_r)
                    if success:
                        st.success(msg)
                        st.rerun()
                    else:
                        st.error(msg)
                else:
                    st.warning("Isi semua kolom!")

    # Bagian 3: Hapus User
    with st.expander("🗑️ HAPUS USER"):
        user_to_del = st.selectbox("Pilih User untuk Dihapus", users_df['username'].tolist())
        if st.button("KONFIRMASI HAPUS", type="primary"):
            if user_to_del == "admin": # Proteksi agar admin utama tidak terhapus
                st.error("Admin utama tidak boleh dihapus!")
            else:
                delete_user(user_to_del)
                st.success(f"User {user_to_del} telah dihapus.")
                st.rerun()

def save_theme(theme_dict):
    with open("theme_config.json", "w") as f:
        json.dump(theme_dict, f)

def load_theme():
    if os.path.exists("theme_config.json"):
        with open("theme_config.json", "r") as f:
            return json.load(f)
    return None # Jika belum ada file, gunakan default

def get_contrast_color(hex_color):
    """Mengembalikan 'white' untuk background gelap dan 'black' untuk background terang"""
    hex_color = hex_color.lstrip('#')
    r, g, b = tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))
    
    # Rumus Standar Luminance (YIQ)
    luminance = (r * 0.299 + g * 0.587 + b * 0.114) / 255
    return "white" if luminance < 0.5 else "#1e1e1e"

def generate_invoice_pdf(master_row, detail_df):
    pdf = FPDF()
    pdf.add_page()
    
    # Header Struk
    pdf.set_font("Arial", 'B', 16)
    pdf.cell(0, 10, "FUTUREMART 2026", ln=True, align='C')
    pdf.set_font("Arial", '', 10)
    pdf.cell(0, 5, "Sistem Kasir Futuristik Terintegrasi", ln=True, align='C')
    pdf.ln(10)
    
    # Informasi Transaksi
    pdf.set_font("Arial", 'B', 10)
    pdf.cell(0, 8, f"ID INVOICE : {master_row['id']}", ln=True)
    pdf.set_font("Arial", '', 10)
    pdf.cell(0, 8, f"Tanggal    : {master_row['date']}", ln=True)
    pdf.cell(0, 8, f"Kasir      : {master_row['cashier']}", ln=True)
    pdf.ln(5)
    
    # Tabel Header
    pdf.set_fill_color(200, 200, 200)
    pdf.cell(80, 8, "Nama Produk", 1, 0, 'C', True)
    pdf.cell(20, 8, "Qty", 1, 0, 'C', True)
    pdf.cell(40, 8, "Harga", 1, 0, 'C', True)
    pdf.cell(45, 8, "Subtotal", 1, 1, 'C', True)
    
    # Tabel Detail
    for _, row in detail_df.iterrows():
        pdf.cell(80, 8, str(row['product_name']), 1)
        pdf.cell(20, 8, str(row['qty']), 1, 0, 'C')
        pdf.cell(40, 8, f"Rp {row['price']:,.0f}", 1, 0, 'R')
        pdf.cell(45, 8, f"Rp {row['subtotal']:,.0f}", 1, 1, 'R')
    
    # Total
    pdf.ln(5)
    pdf.set_font("Arial", 'B', 12)
    pdf.cell(140, 10, "GRAND TOTAL", 0, 0, 'R')
    pdf.cell(45, 10, f"Rp {master_row['total']:,.0f}", 1, 1, 'R')
    
    return pdf.output(dest='S').encode('latin-1')


def show_transaction_history():
    t = st.session_state.theme
    st.markdown(f"<h2 style='color:{t['primary_color']}; font-family:Orbitron;'>📜 TRANSACTION HISTORY</h2>", unsafe_allow_html=True)
    
    search_id = st.text_input("CARI ID INVOICE", placeholder="TRX-XXXX")
    
    if search_id:
        conn = get_connection()
        master = pd.read_sql("SELECT * FROM trans_master WHERE id = ?", conn, params=(search_id,))
        
        if not master.empty:
            st.success(f"Invoice Ditemukan")
            detail = pd.read_sql("SELECT * FROM trans_detail WHERE trans_id = ?", conn, params=(search_id,))
            
            # Area Preview
            st.table(detail[['product_name', 'qty', 'price', 'subtotal']])
            
            # Tombol Cetak (Reprint)
            pdf_data = generate_invoice_pdf(master.iloc[0], detail)
            
            st.download_button(
                label="📥 REPRINT INVOICE (PDF)",
                data=pdf_data,
                file_name=f"Invoice_{search_id}.pdf",
                mime="application/pdf"
            )
        else:
            st.error("Invoice tidak ditemukan.")


def show_product_search():
    t = st.session_state.theme
    st.markdown(f"<h2 style='color:{t['primary_color']}; font-family:Orbitron;'>🔍 PRODUCT FINDER</h2>", unsafe_allow_html=True)
    
    tab1, tab2 = st.tabs(["[ SCAN BARCODE ]", "[ MANUAL SEARCH ]"])
    
    conn = get_connection()
    
    with tab1:
        st.info("Simulasi: Masukkan angka barcode atau gunakan scanner fisik yang terhubung ke keyboard.")
        barcode_input = st.text_input("SCANNING AREA...", placeholder="Arahkan kursor di sini & scan...", key="barcode_scan")
        
        if barcode_input:
            # Cari berdasarkan barcode (Simulasi: ID produk digunakan sebagai barcode)
            res = pd.read_sql("SELECT * FROM products WHERE id = ?", conn, params=(barcode_input,))
            if not res.empty:
                display_product_card(res.iloc[0])
            else:
                st.warning("Produk tidak terdaftar!")

    with tab2:
        search_name = st.text_input("Cari Nama Produk...")
        if search_name:
            res = pd.read_sql("SELECT * FROM products WHERE name LIKE ?", conn, params=(f'%{search_name}%',))
            if not res.empty:
                for _, row in res.iterrows():
                    display_product_card(row)
            else:
                st.write("Produk tidak ditemukan.")

def display_product_card(row):
    t = st.session_state.theme
    # Card visual untuk produk yang ditemukan
    st.markdown(f"""
        <div style="border: 1px solid {t['primary_color']}; padding: 15px; border-radius: 10px; background: rgba(255,255,255,0.05); margin-bottom: 10px;">
            <h3 style="color:{t['primary_color']}; margin:0;">{row['name']}</h3>
            <p style="margin:5px 0;">Kategori: {row['category']} | <b>Stok: {row['stock']}</b></p>
            <h2 style="margin:0; color:white;">Rp {row['price']:,.0f}</h2>
        </div>
    """, unsafe_allow_html=True)

def generate_invoice_pdf(master_row, detail_df):
    pdf = FPDF()
    pdf.add_page()
    pdf.a
    
    # Header Struk
    pdf.set_font("Arial", 'B', 16)
    pdf.cell(0, 10, "FUTUREMART 2026", ln=True, align='C')
    pdf.set_font("Arial", '', 10)
    pdf.cell(0, 5, "Sistem Kasir Futuristik Terintegrasi", ln=True, align='C')
    pdf.ln(10)
    
    # Informasi Transaksi
    pdf.set_font("Arial", 'B', 10)
    pdf.cell(0, 8, f"ID INVOICE : {master_row['id']}", ln=True)
    pdf.set_font("Arial", '', 10)
    pdf.cell(0, 8, f"Tanggal    : {master_row['date']}", ln=True)
    pdf.cell(0, 8, f"Kasir      : {master_row['cashier']}", ln=True)
    pdf.ln(5)
    
    # Tabel Header
    pdf.set_fill_color(200, 200, 200)
    pdf.cell(80, 8, "Nama Produk", 1, 0, 'C', True)
    pdf.cell(20, 8, "Qty", 1, 0, 'C', True)
    pdf.cell(40, 8, "Harga", 1, 0, 'C', True)
    pdf.cell(45, 8, "Subtotal", 1, 1, 'C', True)
    
    # Tabel Detail
    for _, row in detail_df.iterrows():
        pdf.cell(80, 8, str(row['product_name']), 1)
        pdf.cell(20, 8, str(row['qty']), 1, 0, 'C')
        pdf.cell(40, 8, f"Rp {row['price']:,.0f}", 1, 0, 'R')
        pdf.cell(45, 8, f"Rp {row['subtotal']:,.0f}", 1, 1, 'R')
    
    # Total
    pdf.ln(5)
    pdf.set_font("Arial", 'B', 12)
    pdf.cell(140, 10, "GRAND TOTAL", 0, 0, 'R')
    pdf.cell(45, 10, f"Rp {master_row['total']:,.0f}", 1, 1, 'R')
    
    return pdf.output(dest='S').encode('latin-1')


def get_monthly_report():
    conn = get_connection()
    # Query untuk merekap data per bulan
    query = """
    SELECT 
        strftime('%Y-%m', date) as Bulan,
        COUNT(id) as Total_Transaksi,
        SUM(total + discount) as Pendapatan_Kotor,
        SUM(discount) as Total_Diskon,
        SUM(total) as Pendapatan_Bersih
    FROM trans_master
    GROUP BY Bulan
    ORDER BY Bulan DESC
    """
    df_report = pd.read_sql(query, conn)
    
    # Hitung Total Keseluruhan (Grand Total) untuk baris bawah
    if not df_report.empty:
        summary_row = pd.DataFrame({
            'Bulan': ['TOTAL'],
            'Total_Transaksi': [df_report['Total_Transaksi'].sum()],
            'Pendapatan_Kotor': [df_report['Pendapatan_Kotor'].sum()],
            'Total_Diskon': [df_report['Total_Diskon'].sum()],
            'Pendapatan_Bersih': [df_report['Pendapatan_Bersih'].sum()]
        })
        df_report = pd.concat([df_report, summary_row], ignore_index=True)
    
    return df_report

def to_excel(df):
    output = io.BytesIO()
    # Menulis ke Excel menggunakan engine openpyxl
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Laporan_Keuangan')
    return output.getvalue()

def play_ai_voice(text):
    # JavaScript untuk memicu suara asisten AI
    js_code = f"""
        <script>
        var msg = new SpeechSynthesisUtterance("{text}");
        msg.lang = 'id-ID';  // Bahasa Indonesia
        msg.pitch = 1.2;     // Sedikit tinggi agar terdengar futuristik
        msg.rate = 1.0;
        window.speechSynthesis.speak(msg);
        </script>
    """
    st.components.v1.html(js_code, height=0)

def play_tech_chime():
    # Suara notifikasi modern yang bersih
    audio_url = "https://actions.google.com/sounds/v1/alarms/beep_short.ogg" # Ganti dengan URL suara synth yang Anda suka
    audio_html = f"""
        <audio autoplay>
            <source src="{audio_url}" type="audio/ogg">
        </audio>
    """
    st.components.v1.html(audio_html, height=0)

def play_ai_voice_feminin(text):
    js_code = f"""
        <script>
        function speak() {{
            var msg = new SpeechSynthesisUtterance("{text}");
            var voices = window.speechSynthesis.getVoices();
            
            // Kriteria pencarian suara: Indonesia + (Gadis / Google / Female)
            var selectedVoice = voices.find(function(v) {{
                return v.lang.includes('id') && 
                       (v.name.includes('Gadis') || v.name.includes('Google') || v.name.includes('Indonesian'));
            }});

            if (selectedVoice) {{
                msg.voice = selectedVoice;
            }}

            // Paksa pitch lebih tinggi agar tetap feminin jika suara default terpilih
            msg.pitch = 1.3; 
            msg.rate = 0.9;
            msg.lang = 'id-ID';
            
            window.speechSynthesis.speak(msg);
        }}

        // Menangani delay loading suara di browser
        if (window.speechSynthesis.onvoiceschanged !== undefined) {{
            window.speechSynthesis.onvoiceschanged = speak;
        }}
        
        // Coba jalankan langsung jika sudah termuat
        if (window.speechSynthesis.getVoices().length > 0) {{
            speak();
        }}
        </script>
    """
    st.components.v1.html(js_code, height=0)


def play_alert_sound():
    # Menggunakan suara 'beep' pendek dalam format base64 agar tidak perlu file eksternal
    audio_html = """
        <audio autoplay>
            <source src="https://www.soundjay.com/buttons/beep-01a.mp3" type="audio/mpeg">
        </audio>
    """
    st.components.v1.html(audio_html, height=0)


def check_low_stock_alerts(threshold=5):
    conn = get_connection()
    low_stock_df = pd.read_sql("SELECT name FROM products WHERE stock <= ?", conn, params=(threshold,))
    
    if not low_stock_df.empty:
        # Mengambil satu nama produk untuk disebut oleh AI
        #produk_list = ", ".join(low_stock_df['name'].tolist()[:6]) # Ambil [:6] produk pertama saja agar tidak kepanjangan
        produk_list = ", ".join(low_stock_df['name'])
        #pesan = f"Peringatan sistem. Stok {produk_list} hampir habis. Segera lakukan pengisian."
        pesan = f"Maaf, peringatan stok. Produk {produk_list} sudah mencapai batas minimum. Mohon segera melakukan pemesanan ulang."
        
        
        #play_ai_voice(pesan)
        #play_tech_chime()
        play_ai_voice_feminin(pesan)
        
        # UI Alert yang cantik
        st.toast(f"🚨 {pesan}", icon="⚠️")
        
        # Tampilan Visual (Blinking Red)
        st.markdown(f'''
            <div style="background: rgba(255,0,0,0.2); border-left: 5px solid red; padding: 10px; border-radius: 5px;">
                <h4 style="color: #ff4b4b; margin:0;">🚨 SYSTEM NOTIFICATION</h4>
                <p style="margin:0;">{pesan}</p>
            </div>
        ''', unsafe_allow_html=True)



def show_financial_report():
    t = st.session_state.theme
    st.markdown(f"<h2 style='color:{t['primary_color']}; font-family:Orbitron;'>📊 FINANCIAL REPORT</h2>", unsafe_allow_html=True)
    
    report_df = get_monthly_report()
    
    if not report_df.empty:
        # Tampilan tabel di Streamlit
        st.dataframe(report_df.style.format({
            'Pendapatan_Kotor': 'Rp {:,.0f}',
            'Total_Diskon': 'Rp {:,.0f}',
            'Pendapatan_Bersih': 'Rp {:,.0f}'
        }), width='stretch')
        
        # Tombol Download Excel
        excel_data = to_excel(report_df)
        st.download_button(
            label="📈 DOWNLOAD LAPORAN EXCEL",
            data=excel_data,
            file_name=f"Laporan_FutureMart_{datetime.now().strftime('%Y%m%d')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    else:
        st.info("Belum ada data transaksi untuk dilaporkan.")



# --- 5. SETTINGS (Termasuk Warna Body) ---
def show_settings():
    st.title("⚙️ SYSTEM CONFIG")
    t = st.session_state.theme
    st.markdown('<div class="glass-card">', unsafe_allow_html=True)
    
    st.subheader("Visual Identity")
    c1, c2, c3 = st.columns(3)
    new_primary = c1.color_picker("Top Bar", t['top_bar'])
    new_bg = c2.color_picker("Sidebar/Base", t['bg_color'])
    new_body = c3.color_picker("Main Page Body", t['body_color'])
    
    _="""
    if st.button("APPLY SETTINGS"):
        st.session_state.theme.update({
            'primary_color': new_primary, 
            'bg_color': new_bg, 
            'body_color': new_body,
            'top_bar': new_primary
        })
        st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)
    """

    if st.button("SIMPAN PERUBAHAN TEMA"):
        st.session_state.theme['bg_color'] = new_bg
        st.session_state.theme['body_color'] = new_body
        st.session_state.theme['primary_color'] = new_primary
        st.session_state.theme['top_bar'] = new_primary
        
        # PERMANENKAN KE FILE
        save_theme(st.session_state.theme)
    
        st.success("Tema Berhasil Disimpan Permanen!")
        st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)

def login_user(username, password):
    conn = get_connection()
    cursor = conn.cursor()
    
    # Cari user berdasarkan username
    cursor.execute("SELECT password, role FROM users WHERE username = ?", (username,))
    result = cursor.fetchone()
    conn.close()

    if result:
        db_password = result[0]
        db_role = result[1]
        # Bandingkan hash password input dengan hash di database
        if hash_password(password) == db_password:
            return True, db_role
    return False, None


# (Main Routing Logic tetap sama seperti sebelumnya)
def app_supermarket():
    st.set_page_config(page_title="FutureMart 2026", layout="wide")
    
    # 1. Pastikan variabel state tersedia
    if 'logged_in' not in st.session_state: st.session_state.logged_in = False
    if 'cart' not in st.session_state: st.session_state.cart = []
    
# Inisialisasi tema dari file atau default
    if 'theme' not in st.session_state:
        saved_theme = load_theme()
        if saved_theme:
            st.session_state.theme = saved_theme
        else:
            st.session_state.theme = {
                'bg_color': '#050a12', 
                'top_bar': '#95A5A6',
                'body_color': '#0a101f', 
                'primary_color': '#00fbff', 
                'font_family': 'Rajdhani', 
                'font_size': '14px'            
            }

    # 3. Pindahkan apply_theme() ke LUAR blok 'if' agar selalu dijalankan
    apply_theme()

# Inisialisasi tema dari file atau default
    if 'theme' not in st.session_state:
        saved_theme = load_theme()
        if saved_theme:
            st.session_state.theme = saved_theme
        else:
            st.session_state.theme = {
                'bg_color': '#050a12', 
                'top_bar': '#95A5A6',
                'body_color': '#0a101f', 
                'primary_color': '#00fbff', 
                'font_family': 'Rajdhani', 
                'font_size': '14px'            
            }

    if not st.session_state.logged_in:
            st.components.v1.html(
                """
                <script>
                    var input = window.parent.document.querySelectorAll("input[type=text]");
                    for (var i = 0; i < input.length; ++i) {
                        if (input[i].getAttribute('aria-label') === "Username") {
                            input[i].focus();
                        }
                    }
                </script>
                """,
                height=0,
            )            
            st.markdown("<h1 style='text-align:center;'>LOGIN SYSTEM</h1>", unsafe_allow_html=True)
            _, col, _ = st.columns([1,1,1])
            with col.form("login"):
                u = st.text_input("Username", placeholder="Enter username...")
                p = st.text_input("Password", type="password", placeholder="Enter password...")
                if st.form_submit_button("MASUK"):
                    is_valid, role = login_user(u, p)
                    if is_valid:
                        st.session_state.logged_in = True
                        st.session_state.username = u
                        st.session_state.user_role = role         
                        add_log(u, "LOGIN") # <--- CATAT LOGS LOGIN USER                 
                        st.success(f"Selamat Datang, {u} ({role})")
                        st.rerun()
                    else:
                        st.error("Username atau Password Salah!")



    else:
        with st.sidebar:
            st.markdown(f"<h1 style='color:var(--primary); font-family:Orbitron;'>FM-POS</h1>", unsafe_allow_html=True)
            st.write(f"User: **{st.session_state.username}** ({st.session_state.user_role})")
            
            # Dynamic Menu based on Role
            menu_options = ["Dashboard", "Transaction","Riwayat", "Cari Produk",  "Settings"]
            if st.session_state.user_role == 'Admin':
                menu_options += ["Forecasting", "Inventory",
                            "Laporan Keuangan", "Backup_DB_Online", 
                            "Accounting", "User Mgmt","Activity Logs"]

                                
            menu = st.radio("Menu", menu_options)
            if st.button("LOGOUT"):
                add_log(st.session_state.username, "LOGOUT") # <--- CATAT LOGOUT
                st.session_state.logged_in = False
                for key in list(st.session_state.keys()):
                    del st.session_state[key]                
                st.rerun()



        # Routing Logic
        if menu == "Dashboard": show_dashboard()
        elif menu == "Forecasting": show_forecasting()
        elif menu == "Transaction": show_pos()
        elif menu == "Inventory": show_inventory()
        elif menu == "Riwayat": show_transaction_history()
        elif menu == "Cari Produk": show_product_search()
        elif menu == "Laporan Keuangan": show_financial_report()
        elif menu == "Backup_DB_Online": show_database_tools()        
            #st.write("Backup_DB_Online (Admin Only)")
        elif menu == "Accounting": show_accounting() 
            #st.write("Accounting Module (Admin Only)")
        elif menu == "User Mgmt": show_user_mgmt()
        elif menu == "Settings": show_settings()
            #st.write("Settings Module (Admin Only)")
        elif menu == "Activity Logs": show_activity_logs()    

if __name__ == "__main__":
    app_supermarket()