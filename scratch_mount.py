import math

def calculate_host_slots(plate_width, plate_height):
    # Round up to nearest 20mm multiple for wing spacing
    # Add 15mm base clearance to clear motor body + bolt heads
    spacing = math.ceil((plate_width + 15) / 20) * 20 
    
    overall_width = spacing + 20 # 10mm material outside each slot
    
    # 2 slots, centered on Y, at +/- spacing/2 on X
    slots = [
        (-spacing/2, 0, 15, 5.5, "y"), # 15mm long, 5.5mm wide (M5 clearance), oriented along Y
        (spacing/2, 0, 15, 5.5, "y")
    ]
    
    return overall_width, slots

print("NEMA 17 (42.3):", calculate_host_slots(42.3, 42.3))
print("NEMA 23 (56.4):", calculate_host_slots(56.4, 56.4))
