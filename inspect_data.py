import sqlite3


DATABASE = "issues.db"

conn = sqlite3.connect(DATABASE)


# Total number of issues
cursor = conn.execute("""
    SELECT COUNT(*)
    FROM issues
""")

total = cursor.fetchone()[0]

print(f"\nTotal issues: {total}")


# Number of issues per Product Area
print("\nIssues per Product Area:\n")

cursor = conn.execute("""
    SELECT product_area, COUNT(*) AS count
    FROM issues
    GROUP BY product_area
    ORDER BY count DESC
""")

for product_area, count in cursor.fetchall():
    print(f"{count:4}  {product_area}")


# Show 5 example issues
print("\n\nExample issues:\n")

cursor = conn.execute("""
    SELECT number, title, product_area
    FROM issues
    LIMIT 5
""")

for number, title, product_area in cursor.fetchall():
    print(f"#{number}")
    print(f"Title: {title}")
    print(f"Label: {product_area}")
    print()


conn.close()