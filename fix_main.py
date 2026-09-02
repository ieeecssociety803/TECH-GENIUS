with open('backend/app/main.py', 'r', encoding='utf-8') as f:
    content = f.read()

content = content.replace('prefix=f\"{settings.API_V1_STR}/risk\"', 'prefix=settings.API_V1_STR')
content = content.replace('prefix=f\"{settings.API_V1_STR}/gis\"', 'prefix=settings.API_V1_STR')
content = content.replace('prefix=f\"{settings.API_V1_STR}/alerts\"', 'prefix=settings.API_V1_STR')
content = content.replace('prefix=f\"{settings.API_V1_STR}/predict\"', 'prefix=settings.API_V1_STR')

with open('backend/app/main.py', 'w', encoding='utf-8') as f:
    f.write(content)
print("Success")
