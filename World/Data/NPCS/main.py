from npc_generator import generate_npc

npc = generate_npc()

print("NPC Generated")
print("-------------")

for key, value in npc.items():
  print(f"{key}: {value}")
