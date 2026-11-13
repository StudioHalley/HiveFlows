#Question

On est avancé. Il faut réfléchir si on est dans la bonne track.

On a pas terminé, mais il faut s'assurer qu'on ne rate pas la cible.

Le site va devoir permettre plusieurs agents-AI et plusieurs humains dans la même conversation. 

On va devoir trouver une facon, pour que tous les Agents ne répondre en même temps, et en bouche.  J'avais pensé que les utilisateurs pourrait  utilisé le symbole @ pour interpeler une AI. Par exemple, l'agent avec le slug time-traveler, devrait répondre lorsque si quelqu'un écrit "Hey @time-traveler comment ca va ?"

Lorsqu'un seul humain, est avec un seul agent, pas besoin d'utilisé le @.

Ume autre facon, qui serait compatible avec l'idée des @, c'est qu'un agent "principale" écoute en silence la discution, et dispatch au besoin. Par exemple, si une personne demand la météo, l'agent principale pourrait lancer la requête à un "subagent", qui a dans ses tools les prédiction météo. Cette facon, ca peut être fait plus tard... c'est plus compliqué que le @ je crois.

D'ailleur parlant d'agent princiale, je crois que ca prendrait un agent par défaut, le même dans touts les chat/workspace. Car pour le UX, arriver et qu'il n'y est pas d'agent, c'est pas l'idéal. Je crois que meme avant d'avoir compris l'emsemble de la plateforme, un nouvel usager doit déjà pouvoir utilisé quelques fonctionnalité de base. On devrait ajouter une permission "site-admin", qui pourrait configuré cet agent. D'ailleur cette permission servira probablement a plein d'autre taches éventuellement (bannir user, voir logs, etc)


Aussi les agents sont isoler par workspace.  Je me rend compte que c'est pas l'idéal. Il faudrait que les agents soit configurable au niveau de l'utilisateur, et qu'on puisse ajouter/inviter les agents à la discussion.  Aussi, ca prendrait une facon de les enlever.

Exemple : l'utilisateur @a créé le workspace #ws1. 
puis utilisateur @a invite l'utilisateur @b à se joindre. L'utilisateur @b ajoute un de ses agents dans le workspace.  

Choses qu'ils faut prévoir :

il ne faut pas que les slug des usager et des agent se recoupe... L'application télégram a une facon de le faire pour ses bot. Leur slug se termine par "_bot", et seul les bots peuvent avoir cela. Je ne suis pas certains que c'est la solution que je veux. Mais je vais quand meme la considéré.

ajouter des slash commands. Ca donne une vibe "mIRC" et j'aime ca.
/invite @slugusername
/invite @slugagentname
/part    <== quitter un workspace
/join #workspace
/kick @slug

d'autres commandes s'ajouterons en cours de projet. pourquoi pas laisser les usagés créé leur propre alias, pour se créé des commandes personnalisées.


ca va prendre une page "compte/account" ou pourra changer ses infos, (exemple courriel)


chaque workspace devra avoir un répertoire sur le disque du serveur. Les agents pourront intégragir avec des fichiers de ces répertoires.

un workspace est persistant. Lorsqu'un utilisateur revient, il peut. voir l'historique de toute la discussion, meme la partie ou il était absent. Ca va devenir "lourd" alors il faut penser a un systeme de paging. Qui sera transparent pour l'utilisateur, il scroll pour revoir le passé, et ca se load au fur et a mesure.

Aussi, comme la discussion peut être infini, il ne faut pas que les agents utilises l'ensemble de la discussion pour leur LLM, il faudrait un cut-off. On pourrait aussi penser a une facon qu'il recherche dans l'ensemble de la discussion lorsque nécessaire.

OK, ca fait bcp à penser déjà. Je veux que tu prennes un pas de recule, que tu essaie de voir le projet a 360 degrée. Voir ce qui a été fait, ce qu'on a de prévu, et qu'on s'asssure que ce que je viens de dire s'y intégre. Il faudra probablement ajuster le plan (GOAL.md) A