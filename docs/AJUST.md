Le projet va bon train. La phase 4 est terminé avec succès.

Avant de commencer la phase 5, j'aimerais qu'on fasse un petit "sprint" d'amélioration afin de de rentre l'expérience utilisateur encore meilleur.

Premièrement la disposition de UI. 

Dans la barre latéral gauche, 

"Conversation" c'est plus utilse car on a maintenant une seule conversation infini pour chaque workspace. Par contre j'aime la disposition en tableau.  On pourrait conserverver cette écran, pour afficher tous les workspaces de l'usager.  Il faudrait ajouter quelques "colonne" relatif aux workspace : une colonne role (le rôle de l'utilisateur courant dans ce workspace), nombre de membre, nombre d'agent, et aussi un bouton "modifier" (si les permission le permettre.)

On eneleve le dropbox "agent" de l'écran "conversation". La recherche, va rechercher dans tous les messages, de tous les workspace. On affiche seulement les workspace dont il y a un match de recherche, avec le derniere message correspondant a la recherche. (toujours dans la meme écran, on fait seulement rafraichir le tableau)

Si on clique sur une ligne du tableau, on navigue jusque dans le chat de du workspace (sauf le bouton modifier)

Avec ces changements, les menu Conversation et Workspace de la barre latéral deviennent un peu redondant. Il faudrait les "Fusionner" On conserver "workspace" (/dashboard/workspace), mais qui va afficher par défaut le visuel de Conversation (avec les changement indiqué précédament) 
La première colonne "agent" devrait s'appeler "workspace" et afficher le nom du workspace.

Pour la suite des instructions, je vais me référé à "workspace unifié" pour parler de cette écran.

Dans le haut de l'ércan, il faut "Nouveau Workspace", ainsi qu'un autre bouton qui va permettre de basculer de "view" : Liste et Tuile. "Liste' est es la vue en tableau (comme conversation) et "tuile" est la vue comme l'ancienne écran workspace. 

Présentement Voir (workspace) nous amene a cet écran
/dashboard/workspaces/{id}

et Modifier nous amene a cette écran
dashboard/workspaces/{id}/edit

Ces 2 écran devrait être aussi fusionner. Un seul écran pour géré les workspace.

Dans la liste, les bouton "Voir" nous amene dans le chat, et modifier, nous amene a l'écran de gestion unifié du workspace

