def _construir_linea_stats(self, data):
    # Replace emoji reactions with custom image icons
    reactions = []
    for item in data:
        if item['reaction'] == '❤️':
            reactions.append('https://i.ibb.co/HTtmtcw2/corazon.png')
        elif item['reaction'] == '💬':
            reactions.append('https://i.ibb.co/Gv4w3q60/imgi-1-875080101035917332.png')
        elif item['reaction'] == '👁️':
            reactions.append('https://i.ibb.co/c0Dfc9H/imgi-1-1045689074272444507.png')
    return reactions
