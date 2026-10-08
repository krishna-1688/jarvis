"""
How students actually ask, and where each question must land. Used by
test_routing.py. "brain" = plain chat (with the student's data attached
when the question is academic). A set means any of those intents gives a
correct answer. Add a case whenever a real question is routed wrong.
"""

ATT = {"attendance"}
BUNK = {"bunk_check"}
MARKS = {"vtop_marks"}
GRADES = {"grade_history", "sem_gpa"}
CGPA = {"cgpa"}
SEMGPA = {"sem_gpa"}
EXAMS = {"exams"}
TT_TODAY = {"timetable_today", "schedule_today"}
TT_TOM = {"timetable_tomorrow", "schedule_tomorrow"}
TT_WEEK = {"timetable_week", "schedule_week"}
NEXT = {"next_class", "schedule_next"}
AT = {"class_at_time"}
LMS = {"lms_assignments"}
PRED = {"cgpa_predict"}
TARGET = {"grade_target"}
BEST = {"best_case_cgpa"}
OVR = {"overall_cgpa_target"}
BRAIN = {"brain"}

CASES = [
    # ── attendance ──
    ("what's my attendance", ATT), ("attendance", ATT), ("show my attendance", ATT),
    ("how is my attendance looking", ATT), ("attendance in DAA", ATT), ("what's my DAA attendance", ATT),
    ("how many classes have I missed in probability", ATT), ("am I below 75 anywhere", ATT),
    ("which subject has the lowest attendance", ATT), ("am I in danger of debarment", ATT),
    ("how many classes did I attend in cloud", ATT), ("attendance percentage for stats", ATT),
    ("my attendance in sem 4", ATT), ("is my attendance ok", ATT), ("how many classes absent in DAA lab", ATT),
    ("tell me attendance for all courses", ATT), ("whats my overall attendance percentage", ATT),
    ("did my attendance drop", ATT), ("check attendance", ATT), ("how much attendance do I have in daa", ATT),
    # ── bunk ──
    ("can I bunk DAA tomorrow", BUNK), ("can I skip probability today", BUNK), ("how many classes can I skip in DAA", BUNK),
    ("which class is safest to bunk", BUNK), ("can I miss tomorrow's classes", BUNK), ("is it safe to skip cloud",
    BUNK), ("how many more classes can I miss and still be above 75", BUNK), ("how many DAA classes do I need to attend to reach 75", BUNK | ATT),
    ("can I take a leave tomorrow", BUNK | BRAIN), ("should I go to class tomorrow or can I skip", BUNK),
    # ── current-sem marks ──
    ("what are my marks", MARKS), ("my CAT1 marks", MARKS), ("what did I get in DAA CAT1", MARKS),
    ("how much did I score in CAT 2", MARKS), ("show my DA marks", MARKS), ("marks in probability", MARKS),
    ("how did I do in the cloud CAT", MARKS), ("internal marks for DAA", MARKS), ("my quiz marks", MARKS),
    ("what's my total internal in DAA", MARKS), ("show all my marks this semester", MARKS),
    ("how much did I get in assignment 1", MARKS), ("my lab marks", MARKS), ("what's my score in cat1 for stats", MARKS),
    ("did I pass CAT1", MARKS), ("what is my highest mark", MARKS), ("which subject did I score lowest in cat 1", MARKS),
    ("marks", MARKS), ("my results", MARKS | GRADES | BRAIN),
    # ── grades / past sems ──
    ("what are my sem 3 marks", GRADES), ("sem 2 grades", GRADES), ("what grades did I get in sem 1", GRADES),
    ("show my grades", GRADES), ("what grade did I get in calculus", GRADES), ("grade history", GRADES),
    ("which subjects did I get B", GRADES), ("in which courses did I get S grade", GRADES),
    ("tell me the subjects which I got C", GRADES), ("did I get an A in calculus", GRADES),
    ("do I have any arrears", GRADES), ("which subject i got backlog", GRADES), ("any N1 grade", GRADES),
    ("did I fail any subject", GRADES), ("how many S grades do I have", GRADES), ("what was my grade in DBMS", GRADES),
    ("my 4th sem results", GRADES), ("results of first semester", GRADES), ("what did I get in java", GRADES | MARKS),
    ("show my semester 3 result", GRADES),
    # ── CGPA / GPA ──
    ("what's my CGPA", CGPA), ("cgpa", CGPA), ("tell me my cumulative gpa", CGPA), ("my overall GPA", CGPA),
    ("what is my current CGPA", CGPA), ("how much CGPA do I have", CGPA),
    ("what's my sem 3 GPA", SEMGPA), ("GPA for semester 2", SEMGPA), ("what was my GPA last semester", SEMGPA),
    ("sem 4 gpa", SEMGPA),
    # ── predictions / targets ──
    ("what will my CGPA be if I get S in DAA", PRED), ("if I get an A in cloud what will my cgpa be", PRED),
    ("what do I need in FAT to get an A in DAA", TARGET), ("how much should I score in FAT for S in probability", TARGET),
    ("what will my cgpa be if I get S in all subjects", BEST), ("best case cgpa this sem", BEST),
    ("what GPA do I need this sem to get 9 CGPA", OVR), ("how can I reach 9 cgpa", OVR | BRAIN),
    # ── exams ──
    ("when is my next exam", EXAMS), ("exam schedule", EXAMS), ("when is FAT", EXAMS), ("when does CAT2 start", EXAMS),
    ("when is the DAA exam", EXAMS), ("how many days until my exams", EXAMS), ("where is my exam venue", EXAMS),
    ("what's my seat number for the exam", EXAMS), ("do I have any exams this week", EXAMS), ("FAT timetable", EXAMS),
    # ── timetable ──
    ("what classes do I have today", TT_TODAY), ("today's timetable", TT_TODAY), ("timetable tomorrow", TT_TOM),
    ("what's my first class tomorrow", TT_TOM | AT), ("what's my next class", NEXT), ("where is my next class", NEXT),
    ("do I have DAA on friday", AT), ("am I free at 3pm", AT | {"schedule_free"}), ("what classes on monday", AT | TT_WEEK),
    ("show my weekly timetable", TT_WEEK), ("when is my DAA lab", AT | TT_WEEK), ("what time does my class end today", TT_TODAY | AT),
    ("do I have class now", NEXT | TT_TODAY | AT), ("how many classes tomorrow", TT_TOM),
    # ── LMS ──
    ("any assignments due", LMS), ("pending assignments", LMS), ("what's due on moodle", LMS),
    ("when is the DAA assignment due", LMS), ("do I have any lab submissions this week", LMS),
    # ── unclear / general -> brain ──
    ("how do I improve my attendance", BRAIN | ATT), ("how should I prepare for FAT", BRAIN),
    ("what is a good CGPA for placements", BRAIN), ("explain how VIT grading works", BRAIN),
    ("is 8.5 cgpa good", BRAIN), ("what is relative grading", BRAIN), ("tips to score well in CAT", BRAIN),
    ("I'm stressed about exams", BRAIN), ("motivate me to study", BRAIN), ("what is dynamic programming", BRAIN),
    ("explain Dijkstra's algorithm", BRAIN), ("how many credits do I need to graduate", BRAIN),
    ("what's the difference between CAT and FAT", BRAIN), ("tell me a joke", BRAIN), ("hello", BRAIN),
    ("how are you", BRAIN), ("what should I study tonight", BRAIN | {"schedule_today", "lms_assignments", "exams"}),
    ("I'm scared I'll fail my exam", BRAIN), ("give me a study plan for DAA", BRAIN),
    ("is my attendance enough for me to sit for FAT", ATT | BRAIN), ("will I be debarred", ATT),
    ("mark my words", BRAIN), ("what's a good score in CAT", BRAIN),
    ("which subject should I focus on", BRAIN | MARKS | ATT), ("am I doing well this semester", BRAIN | MARKS | ATT),
]

# Phrasings written after the rules were tuned, to catch overfitting.
HELDOUT = [
    # academic, new wording
    ("yo whats my attendance looking like in compiler", ATT), ("percentage of classes I attended in embedded", ATT),
    ("am I safe in all subjects attendance wise", ATT), ("which courses am I close to 75 in", ATT),
    ("can I bunk the whole day tomorrow", BUNK), ("is it okay if I skip global warming class", BUNK),
    ("how many leaves can I take in DAA", BUNK | ATT),
    ("how much did I score in compiler design cat1", MARKS), ("tell me my embedded CAT marks", MARKS),
    ("what's my DA score in global warming", MARKS), ("show internals", MARKS), ("lowest marks this sem", MARKS),
    ("what did I get in second semester", GRADES), ("give me my previous semester grades", GRADES),
    ("show 1st sem result", GRADES), ("grade in digital systems design", GRADES),
    ("subjects where I scored S", GRADES), ("count of A grades", GRADES), ("did I clear all subjects", GRADES | BRAIN),
    ("any backlogs", GRADES), ("pointer", CGPA | BRAIN), ("what's my pointer", CGPA),
    ("last sem gpa", SEMGPA), ("GPA of 2nd semester", SEMGPA),
    ("if I score S in compiler design what happens to my cgpa", PRED),
    ("how much do I need in FAT to get S in compiler design", TARGET),
    ("when's the next FAT", EXAMS), ("which exam is first", EXAMS), ("cat 2 dates", EXAMS),
    ("which room is my exam in", EXAMS), ("exam hall for DAA", EXAMS),
    ("class schedule for thursday", AT | TT_WEEK), ("any classes on saturday", AT),
    ("what lab do I have tomorrow", TT_TOM | AT), ("am I free after 2pm today", AT | {"schedule_free"}),
    ("which class is next", NEXT), ("first class today", TT_TODAY | AT | NEXT),
    ("do I have any assignment deadline", LMS), ("moodle deadlines", LMS),
    # academic knowledge / advice -> brain
    ("how is CGPA calculated at VIT", BRAIN), ("what happens if attendance is below 75", BRAIN),
    ("what does N1 grade mean", BRAIN), ("how to clear an arrear", BRAIN), ("is 80 percent attendance good", BRAIN),
    ("how should I revise for CAT 2", BRAIN), ("what's the passing mark in FAT", BRAIN),
    ("I feel like I'm going to fail DAA", BRAIN), ("how do I get an S grade", BRAIN),
    # non-academic commands with academic words: must NOT be grabbed
    ("remind me to submit the DA assignment tomorrow", {"task_add"}),
    ("spent 200 on exam fees", {"expense_add"}), ("block 8 to 10 for DAA revision", {"schedule_add"}),
    ("mark the lab record task as done", {"task_complete"}), ("open the VTOP website", {"web"}),
    ("play lofi for studying", {"spotify"}), ("start focus on DAA for 25 minutes", {"focus_start"}),
    ("remember that my FAT hall is AB1 310", {"remember_fact"}),
]
