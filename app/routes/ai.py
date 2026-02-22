# app/routes/ai.py
from flask import Blueprint, render_template, session, request, jsonify, redirect, url_for
from app.services.db import DB_ENGINE
from sqlalchemy import text
from app.services.tasks import process_ai_query
from app.services.ai_context import fetch_context
from app.extensions import get_redis
import time

ai_bp = Blueprint('ai', __name__, url_prefix='/ai')

def check_user_ai_limit(user_id, max_requests=10, period=3600):
    r = get_redis()
    key = f"user_ai_requests:{user_id}"
    now = time.time()
    r.zremrangebyscore(key, 0, now - period)
    if r.zcard(key) >= max_requests:
        return False
    r.zadd(key, {str(now): now})
    r.expire(key, period)
    return True

@ai_bp.route('/')
def index():
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))
    return render_template('ai/ask.html')

@ai_bp.route('/ask', methods=['POST'])
def ask():
    if 'user_id' not in session:
        return jsonify({"error": "Unauthorized"}), 401
    user_id = session['user_id']

    # Rate limit
    if not check_user_ai_limit(user_id):
        return jsonify({
            "status": "limit",
            "message": "You've reached your hourly AI request limit (10). Please try later."
        }), 429

    data = request.get_json()
    question = data.get('question', '').strip()
    if not question:
        return jsonify({"error": "Question cannot be empty"}), 400

    # Determine if deep history needed (simple heuristic)
    use_deep = any(word in question.lower() for word in ['history', 'trend', 'year', 'years', 'long'])

    # Queue task
    task = process_ai_query.delay(user_id, question, use_deep)

    return jsonify({
        "status": "queued",
        "task_id": task.id,
        "message": "Your question is being processed."
    })

@ai_bp.route('/status/<task_id>')
def status(task_id):
    if 'user_id' not in session:
        return jsonify({"error": "Unauthorized"}), 401

    with DB_ENGINE.connect() as conn:
        result = conn.execute(text("""
            SELECT content, status FROM ai_insights
            WHERE task_id = :task_id
        """), {"task_id": task_id}).fetchone()

    if not result:
        return jsonify({"status": "pending"})
    return jsonify({
        "status": result.status,
        "answer": result.content
    })
