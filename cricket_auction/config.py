import os

class Config:
    DB_HOST = os.getenv("DB_HOST")
    DB_PORT = int(os.getenv("DB_PORT", 3306))
    DB_USER = os.getenv("DB_USER")
    DB_PASSWORD = os.getenv("DB_PASSWORD")
    DB_NAME = os.getenv("DB_NAME")

print("HOST =", Config.DB_HOST)
print("PORT =", Config.DB_PORT)
print("USER =", Config.DB_USER)
print("DB =", Config.DB_NAME)