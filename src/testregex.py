import re 
expression = "DTCC said its subsidiary DTC has received a No-Action Letter from the U.S. SEC authorizing a three-year, controlled-production tokenization service for DTC-custodied assets, with rollout expected in the second half of 2026. The authorization permits tokenizing traditional assets on approved L1 and L2 networks, with the digital versions carrying the same rights and protections as their conventional forms. — link"

symbol = "s"
pattern = r'(\$'+symbol+r')|('+symbol+r' (Protocol|Network|Chain|Coin|Token))'

regex = re.search(pattern, expression, re.IGNORECASE)

print(regex)

