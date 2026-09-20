from flask import Flask, render_template, request, session
import fitz
import os
import json
import time
import sqlite3
from dotenv import load_dotenv
from google import genai

# Load .env
load_dotenv()

app = Flask(__name__)
app.secret_key = "adaptive-quiz-secret-key"

UPLOAD_FOLDER = "uploads"
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
# =========================
# DATABASE
# =========================

DATABASE = "quiz_history.db"


def init_database():

    connection = sqlite3.connect(DATABASE)

    cursor = connection.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS quiz_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            quiz_name TEXT,
            difficulty TEXT,
            score INTEGER,
            total INTEGER,
            percentage REAL,
            performance TEXT,
            quiz_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    connection.commit()
    connection.close()


init_database()

# Gemini API key
api_key = os.getenv("GEMINI_API_KEY")

if not api_key:
    raise ValueError("GEMINI_API_KEY not found in .env")

client = genai.Client(api_key=api_key)


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/generate", methods=["POST"])
def generate():

    # -----------------------------
    # 1. Get uploaded PDF
    # -----------------------------
    pdf_file = request.files.get("pdf")

    if not pdf_file or pdf_file.filename == "":
        return "Please upload a PDF."

    if not pdf_file.filename.lower().endswith(".pdf"):
        return "Only PDF files are allowed."

    # -----------------------------
    # 2. Save PDF
    # -----------------------------
    pdf_path = os.path.join(
        app.config["UPLOAD_FOLDER"],
        pdf_file.filename
    )

    pdf_file.save(pdf_path)

    # -----------------------------
    # 3. Extract text from PDF
    # -----------------------------
    try:

        document = fitz.open(pdf_path)

        extracted_text = ""

        for page in document:
            extracted_text += page.get_text()

        document.close()

    except Exception as e:
        return f"PDF processing error: {str(e)}"

    if not extracted_text.strip():
        return """
        <h2>No readable text found</h2>
        <p>This PDF may contain scanned or handwritten pages.</p>
        """

    # -----------------------------
    # 4. Quiz settings
    # -----------------------------
    number_questions = request.form.get(
        "number_questions",
        "5"
    )

    difficulty = request.form.get(
        "difficulty",
        "Beginner"
    )

    # -----------------------------
    # 5. Limit PDF text
    # -----------------------------
    text_for_ai = extracted_text[:15000]

    # -----------------------------
    # 6. Prompt Gemini
    # -----------------------------
    prompt = f"""
You are an educational quiz generator.

Analyze the study material below.

Create exactly {number_questions}
multiple-choice questions.

Difficulty:
{difficulty}

Each question must contain:

- question
- four options
- correct answer
- brief explanation

Return ONLY valid JSON.

Use exactly this format:

[
  {{
    "question": "Question text",
    "options": [
      "Option A",
      "Option B",
      "Option C",
      "Option D"
    ],
    "answer": "Option A",
    "explanation": "Brief explanation"
  }}
]

Do not add markdown.
Do not add ```json.
Do not add any text outside the JSON.

STUDY MATERIAL:

{text_for_ai}
"""

    # -----------------------------
    # 7. Gemini request with retry
    # -----------------------------

    max_retries = 3

    response = None

    for attempt in range(max_retries):

        try:

            print(
                f"Gemini request attempt {attempt + 1}"
            )

            response = client.models.generate_content(
                model="gemini-3.6-flash",
                contents=prompt
            )

            break

        except Exception as e:

            error_message = str(e)

            print(
                f"Gemini error: {error_message}"
            )

            if attempt < max_retries - 1:

                wait_time = 5 * (2 ** attempt)

                print(
                    f"Retrying in {wait_time} seconds..."
                )

                time.sleep(wait_time)

            else:

                return f"""
                <h2>Gemini AI is temporarily unavailable</h2>

                <p>
                The PDF was successfully processed,
                but Gemini could not generate the quiz.
                </p>

                <p>
                Please try again after a short time.
                </p>

                <p>
                Error: {error_message}
                </p>
                """

    # -----------------------------
    # 8. Get Gemini response
    # -----------------------------

    if response is None:
        return "Unable to get response from Gemini."

    ai_text = response.text.strip()

    print("Gemini response:")
    print(ai_text)

    # -----------------------------
    # 9. Clean JSON
    # -----------------------------

    if ai_text.startswith("```json"):

        ai_text = ai_text[7:]

    if ai_text.startswith("```"):

        ai_text = ai_text[3:]

    if ai_text.endswith("```"):

        ai_text = ai_text[:-3]

    ai_text = ai_text.strip()

    # -----------------------------
    # 10. Convert JSON
    # -----------------------------

    try:

        questions = json.loads(ai_text)

    except json.JSONDecodeError:

        return """
        <h2>Quiz formatting error</h2>

        <p>
        Gemini responded, but the response
        was not valid JSON.
        </p>

        <p>
        Please try generating the quiz again.
        </p>
        """

    # -----------------------------
    # 11. Display quiz
    # -----------------------------

    session["questions"] = questions
    session["difficulty"] = difficulty

    return render_template(
    "quiz.html",
    questions=questions,
    difficulty=difficulty
)

# -----------------------------
# Quiz submission
# -----------------------------

@app.route("/submit", methods=["POST"])
def submit():

    questions = session.get("questions", [])

    if not questions:
        return """
        <h2>No quiz found</h2>
        <p>Please generate a quiz first.</p>
        <a href="/">Go Back</a>
        """

    score = 0
    results = []

    for index, question in enumerate(questions):

        user_answer = request.form.get(
            f"question_{index}"
        )

        correct_answer = question["answer"]

        is_correct = (
            user_answer == correct_answer
        )

        if is_correct:
            score += 1

        results.append({
            "question": index + 1,
            "question_text": question["question"],
            "user_answer": user_answer,
            "correct_answer": correct_answer,
            "explanation": question["explanation"],
            "is_correct": is_correct
        })

    total = len(questions)

    percentage = (score / total) * 100

    if percentage >= 80:
        performance = "Excellent"
    elif percentage >= 50:
        performance = "Good"
    else:
        performance = "Needs Improvement"
        # =========================
    # SAVE QUIZ HISTORY
    # =========================

    quiz_name = session.get(
        "quiz_name",
        "AI Generated Quiz"
    )

    connection = sqlite3.connect(DATABASE)

    cursor = connection.cursor()

    cursor.execute("""
        INSERT INTO quiz_history
        (
            quiz_name,
            difficulty,
            score,
            total,
            percentage,
            performance
        )
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        quiz_name,
        session.get("difficulty", "Beginner"),
        score,
        total,
        percentage,
        performance
    ))

    connection.commit()
    connection.close()    


    return render_template(
        "result.html",
        score=score,
        total=total,
        percentage=percentage,
        performance=performance,
        results=results
    )

# -----------------------------
# Run Flask
# -----------------------------

if __name__ == "__main__":
    app.run(debug=True)