"""The name pools Null Meridian's people are drawn from.

Common given and family names, combined at random by the seeded generator in
:mod:`app.demo.null_meridian.canon`. No pairing is taken from a real directory: the pools
are independent lists, and which given name meets which family name is decided by the seed.

**The order of these tuples is part of the canon.** The generator indexes into them, so
reordering, inserting or removing an entry renames people. Append-only edits would still
rename people, because the pool sizes are moduli. Any change here is a new canon version
and needs a new seed (``null-meridian-v2``), not an edit to ``v1``.
"""

from __future__ import annotations

from typing import Final

# fmt: off
GIVEN_NAMES: Final[tuple[str, ...]] = (
    "Aaron", "Abigail", "Adam", "Adrian", "Aisha", "Alan", "Alejandro", "Alex", "Alexis",
    "Alicia", "Amara", "Amir", "Amy", "Ana", "Andre", "Andrea", "Andrew", "Angela", "Anil",
    "Anita", "Anna", "Anthony", "Arjun", "Ashley", "Ava", "Ben", "Beatriz", "Bianca", "Blake",
    "Brandon", "Brian", "Brianna", "Caleb", "Camila", "Carlos", "Carmen", "Caroline", "Chen",
    "Chloe", "Chris", "Christina", "Claire", "Connor", "Craig", "Daniel", "Daniela", "Darius",
    "David", "Deepa", "Derek", "Diana", "Diego", "Dmitri", "Dominic", "Elena", "Eli", "Elijah",
    "Elise", "Emily", "Emma", "Eric", "Erin", "Ethan", "Eva", "Evan", "Farah", "Fatima",
    "Felix", "Fernando", "Fiona", "Gabriel", "Gabriela", "Grace", "Grant", "Hana", "Hannah",
    "Harper", "Hassan", "Hector", "Helen", "Henry", "Hiro", "Hugo", "Ian", "Imani", "Ines",
    "Iris", "Isaac", "Isabel", "Ivan", "Jack", "Jacob", "Jade", "Jamal", "James", "Jasmine",
    "Jason", "Javier", "Jenna", "Jennifer", "Jesse", "Jessica", "Jin", "Joanna", "Joel",
    "John", "Jordan", "Jose", "Joseph", "Joshua", "Julia", "Julian", "Justin", "Kai", "Karen",
    "Karim", "Kate", "Kayla", "Keith", "Kenji", "Kevin", "Kim", "Kiran", "Kyle", "Lara",
    "Laura", "Lauren", "Leah", "Leo", "Leon", "Liam", "Lily", "Lina", "Lucas", "Lucia",
    "Luis", "Luke", "Maya", "Madison", "Malik", "Marco", "Maria", "Mariana", "Mark", "Marta",
    "Mason", "Matthew", "Megan", "Mei", "Melissa", "Mia", "Michael", "Michelle", "Miguel",
    "Mina", "Mohammed", "Monica", "Nadia", "Naomi", "Nathan", "Neha", "Nicholas", "Nicole",
    "Nina", "Noah", "Nora", "Olivia", "Omar", "Oscar", "Owen", "Paolo", "Patrick", "Paul",
    "Priya", "Rachel", "Rafael", "Rahul", "Ravi", "Rebecca", "Reza", "Ricardo", "Riley",
    "Rita", "Robert", "Rosa", "Ruby", "Ryan", "Sakura", "Samantha", "Samir", "Samuel", "Sara",
    "Sean", "Sebastian", "Sergei", "Shreya", "Simone", "Sofia", "Sophie", "Stefan", "Stephen",
    "Sunil", "Tanya", "Taylor", "Teresa", "Thomas", "Tiago", "Tom", "Tyler", "Valentina",
    "Victor", "Victoria", "Vikram", "Wei", "William", "Xavier", "Yara", "Yusuf", "Zara",
    "Zoe",
)

FAMILY_NAMES: Final[tuple[str, ...]] = (
    "Abbott", "Acosta", "Adams", "Adeyemi", "Aguilar", "Ahmed", "Akhtar", "Alexander", "Ali",
    "Allen", "Alvarez", "Anderson", "Andrews", "Arnold", "Bailey", "Baker", "Banerjee",
    "Barnes", "Bauer", "Becker", "Bell", "Bennett", "Berg", "Bishop", "Black", "Boyd",
    "Bradley", "Brennan", "Brooks", "Brown", "Bryant", "Burke", "Butler", "Byrne", "Campbell",
    "Carlson", "Carter", "Castillo", "Castro", "Chandra", "Chang", "Chen", "Cho", "Clark",
    "Cole", "Coleman", "Collins", "Cooper", "Cruz", "Cunningham", "Dang", "Das", "Davies",
    "Davis", "Delgado", "Diaz", "Dixon", "Doherty", "Dubois", "Duncan", "Dunn", "Edwards",
    "Ellis", "Evans", "Farouk", "Ferguson", "Fernandez", "Fischer", "Fleming", "Flores",
    "Ford", "Foster", "Fox", "Franklin", "Fraser", "Garcia", "Gardner", "Garza", "Ghosh",
    "Gibson", "Gomez", "Gonzalez", "Gordon", "Graham", "Grant", "Gray", "Green", "Griffin",
    "Gupta", "Gutierrez", "Hall", "Hamilton", "Hansen", "Harris", "Hart", "Hayes", "Henderson",
    "Hernandez", "Hill", "Ho", "Hoffman", "Holmes", "Hossain", "Howard", "Huang", "Hughes",
    "Hunt", "Hussain", "Ibrahim", "Ito", "Iyer", "Jackson", "James", "Jensen", "Jimenez",
    "Johnson", "Jones", "Joshi", "Kang", "Kapoor", "Kaur", "Keller", "Kelly", "Khan", "Kim",
    "King", "Klein", "Knight", "Kowalski", "Kumar", "Lambert", "Larsen", "Lee", "Lewis", "Li",
    "Lim", "Lin", "Liu", "Lopez", "Lynch", "Malik", "Mann", "Marshall", "Martin", "Martinez",
    "Mason", "Matthews", "Mehta", "Mendez", "Meyer", "Miller", "Mitchell", "Molina", "Moore",
    "Morales", "Moreno", "Morgan", "Morris", "Murphy", "Murray", "Nair", "Nakamura", "Nguyen",
    "Nielsen", "Novak", "Okafor", "Okoye", "Oliveira", "Olsen", "Ortiz", "Owens", "Palmer",
    "Park", "Parker", "Patel", "Pearson", "Perez", "Perry", "Peters", "Petrov", "Phillips",
    "Pierce", "Porter", "Price", "Quinn", "Rahman", "Ramirez", "Ramos", "Rao", "Reddy", "Reed",
    "Reyes", "Reynolds", "Richards", "Rivera", "Roberts", "Robinson", "Rodriguez", "Rogers",
    "Romero", "Rossi", "Russell", "Ryan", "Saito", "Sanchez", "Santos", "Schmidt", "Schneider",
    "Scott", "Shah", "Sharma", "Shaw", "Silva", "Simmons", "Singh", "Smith", "Sokolov",
    "Soto", "Stewart", "Stone", "Sullivan", "Suzuki", "Tanaka", "Taylor", "Thomas",
    "Thompson", "Torres", "Tran", "Turner", "Vargas", "Vasquez", "Verma", "Wagner", "Walker",
    "Wallace", "Walsh", "Wang", "Ward", "Watson", "Weber", "Wells", "West", "White",
    "Williams", "Wilson", "Wong", "Wood", "Wright", "Wu", "Xu", "Yamamoto", "Yang", "Young",
    "Yusuf", "Zhang", "Zhao", "Zimmerman",
)
# fmt: on
