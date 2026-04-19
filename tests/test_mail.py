import smtplib

server = smtplib.SMTP("smtp.gmail.com", 587)
server.starttls()
server.login("ajebofix@gmail.com", "vteckmdusnpogafs")
print("LOGIN SUCCESS")
server.quit()
