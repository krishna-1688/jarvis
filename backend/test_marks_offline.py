"""
Test all marks query types offline — no VTOP, no Groq.
Run after DB is populated.
"""
import sys, os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from core.router import extract_marks_intent
from features.vtop import format_marks_response

tests = [
    # Specific subject + assessment
    "what is my CAT1 mark in calculus",
    "give me TOC cat2",
    "what did i get in technical english FAT",
    "dbms assignment 1",
    "discrete maths cat 1",
    "physics cat2",
    "data structures fat",

    # All marks for a subject
    "give all my calculus marks",
    "show my database marks",
    "toc marks",
    "english marks",
    "chemistry marks",

    # Semester wise
    "sem 1 marks",
    "semester 2 marks",
    "third sem marks",
    "current sem marks",
    "all my marks",

    # Assessment across all subjects in a sem
    "what are the cat1 marks in sem 1",
    "fat marks in sem 2",
    "cat2 marks for sem 4",
    "all fat marks in current sem",

    # Assessment across all sems
    "all my cat1 marks",
    "all fat marks",

    # Subject in specific sem
    "calculus marks in sem 1",
    "dbms marks in sem 4",
    "toc marks in sem 4",

    # Sync
    "refresh marks",
]

print("="*60)
print("OFFLINE MARKS QUERY TEST")
print("="*60)

for query in tests:
    intent = extract_marks_intent(query.lower())
    result = format_marks_response(intent)
    print(f"\n📝 Query: '{query}'")
    print(f"   Intent: type={intent['query_type']} | sub={intent['subject']} | assess={intent['assessment']} | sem={intent['semester']}")
    if result:
        # Show first 3 lines only to keep output readable
        lines = result.split("\n")
        preview = "\n".join(lines[:4])
        if len(lines) > 4:
            preview += f"\n   ... ({len(lines)-4} more lines)"
        print(f"   ✅ {preview}")
    else:
        print(f"   ❌ No data found")