Cette app devra pouvoir être appeler via les tools de LangChain.

human ask => Agent LangChain => Tools LangChain => Celery Worker => Lauch App (docker) 

cette app consiste en un "script" python, qui permet de télécharger un video supporté par yt-dlp.

Tous les infos disponibles sera stocké dans metadata.yaml

Par defaut, on ne fait que télécharger le video, mais des parametres permettrons de spécifier s'il faut extraire le transcript, et/ou seulement l'audio.

Le progress du script sera publier sur redis (voir phase6.md)

output_dir$/ 
    -> video_slug/
        metadata.yaml
        transcript.md
        video_slug_audio.mp3
        vide_slug.mp4


Est-ce qu'on doit buildé une image docker ? ou il existe une existante ? Est-ce qu'on fait une image minimaliste, avec Alpine ? ou bien faudra avoir une distro plus évolué, pour avoir un browser ?
        
Est-ce que le docker restera running ? ou bien on le lance, et il se ferme a chaque utilisation ? (économie de ressource vs rapidité d'éxécution)