"""Anonymous counts of launched assistant functions (not LLM reformulations)."""
import time

LABELS = {
    'weather.forecast': 'Météo', 'wiki.lookup': 'Documentation',
    'llm.chat': 'Conversation', 'test.receive': 'Test de réception',
    'path.message': 'Chemin radio', 'mesh.count_nodes': 'Nombre de nœuds',
    'mesh.count_repeaters': 'Nombre de relais', 'mesh.list_repeaters': 'Liste des relais',
    'mesh.onebyte': 'Routage à un octet', 'mesh.relay_connectivity': 'Classement des relais',
    'mesh.near': 'Nœuds proches', 'mesh.neighbors': 'Voisins directs',
    'mesh.trace': 'Trace radio', 'mesh.advert': 'Annonce du bot',
}


def record(bot, function):
    """Statistics must never interrupt a user's request; store no user or text."""
    try:
        bot.db_manager.execute_update(
            'INSERT INTO assistant_function_usage (timestamp, function) VALUES (?, ?)',
            (time.time(), function),
        )
    except Exception:
        bot.logger.warning('Could not record assistant function usage', exc_info=True)


def top_functions(cursor, window, now=None):
    seconds = {'24h': 86400, '7d': 604800, '30d': 2592000}.get(window)
    where = 'WHERE timestamp > ?' if seconds else ''
    params = ((time.time() if now is None else now) - seconds,) if seconds else ()
    cursor.execute(
        f'SELECT function, COUNT(*) FROM assistant_function_usage {where} '
        'GROUP BY function ORDER BY COUNT(*) DESC, function ASC LIMIT 15', params,
    )
    result = []
    for function, count in cursor.fetchall():
        label = LABELS.get(function, function)
        if function.startswith('mesh.stats.'):
            topic = function.rsplit('.', 1)[-1]
            label = 'Statistiques : ' + {
                'channels': 'canaux', 'messages': 'messages', 'paths': 'chemins',
                'adverts': 'annonces', 'general': 'générales',
            }.get(topic, topic)
        result.append({'command': label, 'function': function, 'count': count})
    return result
