# Hostel Mess Attendance + Waste Forecast

This project is a Flask app for hostel mess attendance and food waste forecasting.

## Run Backend

1. Create and activate a Python virtual environment.
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Start Flask app:

```bash
python app.py
```

Student page: http://127.0.0.1:5000/student

## Student Frontend (Vite + React + Tailwind)

The student-facing page is now built from the frontend app in [frontend](frontend).

Install frontend dependencies:

```bash
cd frontend
npm install
```

Build production assets into Flask static folder:

```bash
npm run build
```

This generates:

- [static/student-dist/student-app.js](static/student-dist/student-app.js)
- [static/student-dist/student-app.css](static/student-dist/student-app.css)

Flask template [templates/index.html](templates/index.html) serves these files directly.

## Admin Frontend (Vite + React + Tailwind + Router)

The admin panel is now a dedicated React build served by Flask.

Build admin production assets into Flask static folder:

```bash
cd frontend
npm run build:admin
```

This generates:

- [static/admin-dist/admin-app.js](static/admin-dist/admin-app.js)
- [static/admin-dist/admin-app.css](static/admin-dist/admin-app.css)

Flask template [templates/admin.html](templates/admin.html) serves these files directly.

To build student + admin bundles together:

```bash
cd frontend
npm run build:all
```

## New Student Data APIs

- `GET /api/student/bootstrap` - student names, meal slots, active/next slot, countdown, menu banner, meal counts, selected student history, impact stat.
- `GET /api/student/history?student_name=...` - 7-day attendance history for one student.

## New Admin Menu API

- `GET /api/admin/menu` - fetch current menu config (admin auth required).
- `POST /api/admin/menu` - update meal menu (admin auth required).

Admin UI now includes a menu editor card in [templates/admin.html](templates/admin.html).