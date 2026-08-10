'''
Author: Nikhita Krisson
Date: 28/06
Purpose: GUI to view and write findings to KB of trading system

run cmd: 
cd /Users/nikhita/13DIT/tradingSystemv0.01
/usr/local/bin/python3 -m src.gui.kb_gui

db: database
kb: knowledge base (the part of the db that will be displayed to user)

'''
#importing libraries-----------------------------------------------
import tkinter as tk

#allows reading and writing findings into the db
from src.data.knowledge_base import get_all_findings, write_finding

DB_PATH = "trading_system.db"
CATEGORIES = ["failure_diagnosis", "market_regime", "parameter_insight", "general"] #values of category column in db

#functions---------------------------------------------------------

def show_findings():
    findings_box.delete(0,tk.END) #clears the listbox
    findings = get_all_findings(DB_PATH, limit=200) #reads from the db
    for f in findings: #looping through each kb finding
        findings_box.insert(tk.END, f"{f['id']} [{f['category']}] {str(f['content'])[:60]}")

def add_finding():
    category= category_box.get() #reads the two text boxes
    content = content_box.get()

    if category not in CATEGORIES:
        status_label.config(text="INVALID CATEGORY: category must be one of the 4 valid ones", fg="red")

    elif content == "":
        status_label.config(text="please type some content.", fg="red")

    else:
        write_finding(category, content, DB_PATH) #writes new finding to db
        status_label.config(text="finding added", fg="green")
        content_box.delete(0, tk.END) #clears the content box
        category_box.delete(0, tk.END) #clears the category box
        show_findings() #refreshes the list box



#window setup------------------------------------------------------
window = tk.Tk()
window.title("Knowledge Base")
window.geometry("640x480")

findings_box = tk.Listbox(window, width=90, height=14)
findings_box.pack(pady=6)
show_findings() #function above that fills the list

tk.Label(window, text="Category:").pack()
category_box = tk.Entry(window, width=30)
category_box.pack()

tk.Label(window, text="Content:").pack()
content_box = tk.Entry(window, width=60)
content_box.pack()

tk.Button(window, text="Add Finding", command=add_finding).pack(pady=6)

status_label = tk.Label(window, text="")
status_label.pack()


window.mainloop()