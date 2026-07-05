import re
text = '[Event "FIDE"] [Site "Bonn"] 1. e4 e5 2. Nf3 Nc6'
text = re.sub(r'\]\s*\[', ']\n[', text)
text = re.sub(r'\]\s+(1\.)', ']\n\n\\1', text)
print(text)
