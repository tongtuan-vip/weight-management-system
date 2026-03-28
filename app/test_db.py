from database import engine

try:
    connection = engine.connect()
    print("Kết nối database thành công!")
    connection.close()

except Exception as e:
    print("Lỗi kết nối database:", e)