def _construir_linea_stats(...):
    ...  # existing code

    # Replace emoji reactions with custom image URLs
    reactions = {
        '👍': 'https://example.com/thumbs_up.png',
        '❤️': 'https://example.com/heart.png',
        '😂': 'https://example.com/laugh.png',
        '😮': 'https://example.com/surprised.png',
        '😢': 'https://example.com/sad.png',
        '😡': 'https://example.com/angry.png',
    }
    ...  # existing code
    for reaction in line:
        if reaction in reactions:
            line[line.index(reaction)] = reactions[reaction]
    ...  # existing code
