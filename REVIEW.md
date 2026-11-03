# The url and api key should be store in .env file for security purpose as it holds confidential information 

SEVERITY (HIGH)

ADVISORY_URL = "http://ml-gateway.internal/classify"
API_KEY = "sk-live-7f3a9c2e41b8d6"

# The string should be keep in some utils or enum file for consistency so that if it changes later we do not require to update the "leagacy_tagger.py" again and again 

SEVERITY (MEDIUM)

UPLOAD_ROOT = "/srv/uploads" 

# All the functions defined in the legacy_tagger.py should be present in some "/utils.py" file and invoke these function in the legacy_tagger or in other modules as well if required for maintaining reusability and follow DRY (Don't Repeat Yourself) principle  

SEVERITY (HIGH)

# The dummy or mock data should be present in mock.py file instead of using it directly in the actual module like "legacy_tagger.py" 

SEVERITY (MEDIUM)

Dummy Data : 

FILENAME_CEL = {
    "w2": "CEL-INC-001",
    "1099": "CEL-INC-002",
    "stmt": "CEL-BNK-001",
    "statement": "CEL-BNK-001",
    "return": "CEL-TAX-001",
    "form": "CEL-TAX-001",
    "id": "CEL-GOV-002",
}