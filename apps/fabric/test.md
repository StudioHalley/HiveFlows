
```shell

root@dockerhost:~# mkdir -p $HOME/.fabric-config
root@dockerhost:~#  docker run --rm -it -v $HOME/.fabric-config:/root/.config/fabric kayvan/fabric:latest --setup
Unable to find image 'kayvan/fabric:latest' locally
latest: Pulling from kayvan/fabric
ed73901162b3: Pull complete 
9916ee584280: Pull complete 
7d7bf0492fa3: Download complete 
Digest: sha256:cee45938a4ec851e58eff36aa43151d18f06c52bbb11247027b0231bd21bdabc
Status: Downloaded newer image for kayvan/fabric:latest

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🎉 Welcome to Fabric! Let's get you set up.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📥 Step 1: Downloading patterns (required for Fabric to work)...

[Patterns Loader]

Enter the default Git repository URL for the patterns (leave empty for 'https://github.com/danielmiessler/fabric.git' or type 'reset' to remove the value):

Enter the default folder in the Git repository where patterns are stored (leave empty for 'data/patterns' or type 'reset' to remove the value):
Downloading patterns and Populating /root/.config/fabric/patterns...


Cloning repository https://github.com/danielmiessler/fabric.git (path: data/patterns)...
Downloaded 251 patterns to temporary directory
✅ Successfully downloaded and installed patterns to /root/.config/fabric/patterns
📝 Created unique patterns file with 251 patterns

📥 Step 2: Downloading strategies (required for Fabric to work)...

[Prompt Strategies]

Enter the default Git repository URL for the strategies (leave empty for 'https://github.com/danielmiessler/fabric.git' or type 'reset' to remove the value):

Enter the default folder in the Git repository where strategies are stored (leave empty for 'data/strategies' or type 'reset' to remove the value):
Downloading strategies and Populating /root/.config/fabric/strategies...


Cloning repository https://github.com/danielmiessler/fabric.git (path: data/strategies)...
Downloaded 9 strategies
✅ Successfully downloaded and installed strategies to /root/.config/fabric/strategies

🤖 Step 3: Configure an AI provider
   Fabric needs at least one AI provider to work.
   You'll be able to add more providers later with 'fabric --setup'


Available AI Providers::



        [1]     Abacus
        [2]     AIML
        [3]     Anthropic
        [4]     Azure
        [5]     AzureAIGateway
        [6]     AzureEntra
        [7]     Bedrock
        [8]     Cerebras
        [9]     Codex
        [10]    Copilot
        [11]    DeepSeek
        [12]    DigitalOcean
        [13]    Exolab
        [14]    Gemini
        [15]    GitHub
        [16]    GrokAI
        [17]    Groq
        [18]    Infermatic
        [19]    Langdock
        [20]    LiteLLM
        [21]    LM Studio
        [22]    Mammouth
        [23]    MiniMax
        [24]    Mistral
        [25]    Novita AI
        [26]    Ollama
        [27]    OpenAI
        [28]    OpenRouter
        [29]    Perplexity
        [30]    SiliconCloud
        [31]    Together
        [32]    Venice AI
        [33]    VertexAI
        [34]    Z AI

[AI Provider Number] Enter the number of the AI provider to configure (leave empty to skip):28

[OpenRouter]

Enter your OpenRouter API KEY (leave empty to skip):123456

Enter your OpenRouter API BASE URL (leave empty for 'https://openrouter.ai/api/v1' or type 'reset' to remove the value):

⚙️  Step 4: Setting default vendor and model...

Available models:

OpenRouter

        [1]     ai21/jamba-large-1.7
        [2]     aion-labs/aion-1.0
        [3]     aion-labs/aion-1.0-mini
        [4]     aion-labs/aion-2.0
        [5]     aion-labs/aion-rp-llama-3.1-8b
        [6]     alfredpros/codellama-7b-instruct-solidity
        [7]     alibaba/tongyi-deepresearch-30b-a3b
        [8]     allenai/olmo-2-0325-32b-instruct
        [9]     allenai/olmo-3-32b-think
        [10]    allenai/olmo-3.1-32b-instruct
        [11]    alpindale/goliath-120b
        [12]    amazon/nova-2-lite-v1
        [13]    amazon/nova-lite-v1
        [14]    amazon/nova-micro-v1
        [15]    amazon/nova-premier-v1
        [16]    amazon/nova-pro-v1
        [17]    anthracite-org/magnum-v4-72b
        [18]    anthropic/claude-3-haiku
        [19]    anthropic/claude-3.5-haiku
        [20]    anthropic/claude-3.7-sonnet
        [21]    anthropic/claude-3.7-sonnet:thinking
        [22]    anthropic/claude-haiku-4.5
        [23]    anthropic/claude-opus-4
        [24]    anthropic/claude-opus-4.1
        [25]    anthropic/claude-opus-4.5
        [26]    anthropic/claude-opus-4.6
        [27]    anthropic/claude-opus-4.6-fast
        [28]    anthropic/claude-sonnet-4
        [29]    anthropic/claude-sonnet-4.5
        [30]    anthropic/claude-sonnet-4.6
        [31]    arcee-ai/coder-large
        [32]    arcee-ai/maestro-reasoning
        [33]    arcee-ai/spotlight
        [34]    arcee-ai/trinity-large-preview:free
        [35]    arcee-ai/trinity-large-thinking
        [36]    arcee-ai/trinity-mini
        [37]    arcee-ai/trinity-mini:free
        [38]    arcee-ai/virtuoso-large
        [39]    baidu/ernie-4.5-21b-a3b
        [40]    baidu/ernie-4.5-21b-a3b-thinking
        [41]    baidu/ernie-4.5-300b-a47b
        [42]    baidu/ernie-4.5-vl-28b-a3b
        [43]    baidu/ernie-4.5-vl-424b-a47b
        [44]    bytedance-seed/seed-1.6
        [45]    bytedance-seed/seed-1.6-flash
        [46]    bytedance-seed/seed-2.0-lite
        [47]    bytedance-seed/seed-2.0-mini
        [48]    bytedance/ui-tars-1.5-7b
        [49]    cognitivecomputations/dolphin-mistral-24b-venice-edition:free
        [50]    cohere/command-a
        [51]    cohere/command-r-08-2024
        [52]    cohere/command-r-plus-08-2024
        [53]    cohere/command-r7b-12-2024
        [54]    deepcogito/cogito-v2.1-671b
        [55]    deepseek/deepseek-chat
        [56]    deepseek/deepseek-chat-v3-0324
        [57]    deepseek/deepseek-chat-v3.1
        [58]    deepseek/deepseek-r1
        [59]    deepseek/deepseek-r1-0528
        [60]    deepseek/deepseek-r1-distill-llama-70b
        [61]    deepseek/deepseek-r1-distill-qwen-32b
        [62]    deepseek/deepseek-v3.1-terminus
        [63]    deepseek/deepseek-v3.2
        [64]    deepseek/deepseek-v3.2-exp
        [65]    deepseek/deepseek-v3.2-speciale
        [66]    eleutherai/llemma_7b
        [67]    essentialai/rnj-1-instruct
        [68]    google/gemini-2.0-flash-001
        [69]    google/gemini-2.0-flash-lite-001
        [70]    google/gemini-2.5-flash
        [71]    google/gemini-2.5-flash-image
        [72]    google/gemini-2.5-flash-lite
        [73]    google/gemini-2.5-flash-lite-preview-09-2025
        [74]    google/gemini-2.5-pro
        [75]    google/gemini-2.5-pro-preview
        [76]    google/gemini-2.5-pro-preview-05-06
        [77]    google/gemini-3-flash-preview
        [78]    google/gemini-3-pro-image-preview
        [79]    google/gemini-3.1-flash-image-preview
        [80]    google/gemini-3.1-flash-lite-preview
        [81]    google/gemini-3.1-pro-preview
        [82]    google/gemini-3.1-pro-preview-customtools
        [83]    google/gemma-2-27b-it
        [84]    google/gemma-2-9b-it
        [85]    google/gemma-3-12b-it
        [86]    google/gemma-3-12b-it:free
        [87]    google/gemma-3-27b-it
        [88]    google/gemma-3-27b-it:free
        [89]    google/gemma-3-4b-it
        [90]    google/gemma-3-4b-it:free
        [91]    google/gemma-3n-e2b-it:free
        [92]    google/gemma-3n-e4b-it
        [93]    google/gemma-3n-e4b-it:free
        [94]    google/gemma-4-26b-a4b-it
        [95]    google/gemma-4-26b-a4b-it:free
        [96]    google/gemma-4-31b-it
        [97]    google/gemma-4-31b-it:free
        [98]    google/lyria-3-clip-preview
        [99]    google/lyria-3-pro-preview
        [100]   gryphe/mythomax-l2-13b
        [101]   ibm-granite/granite-4.0-h-micro
        [102]   inception/mercury
        [103]   inception/mercury-2
        [104]   inception/mercury-coder
        [105]   inflection/inflection-3-pi
        [106]   inflection/inflection-3-productivity
        [107]   kwaipilot/kat-coder-pro-v2
        [108]   liquid/lfm-2-24b-a2b
        [109]   liquid/lfm-2.5-1.2b-instruct:free
        [110]   liquid/lfm-2.5-1.2b-thinking:free
        [111]   mancer/weaver
        [112]   meituan/longcat-flash-chat
        [113]   meta-llama/llama-3-70b-instruct
        [114]   meta-llama/llama-3-8b-instruct
        [115]   meta-llama/llama-3.1-70b-instruct
        [116]   meta-llama/llama-3.1-8b-instruct
        [117]   meta-llama/llama-3.2-11b-vision-instruct
        [118]   meta-llama/llama-3.2-1b-instruct
        [119]   meta-llama/llama-3.2-3b-instruct
        [120]   meta-llama/llama-3.2-3b-instruct:free
        [121]   meta-llama/llama-3.3-70b-instruct
        [122]   meta-llama/llama-3.3-70b-instruct:free
        [123]   meta-llama/llama-4-maverick
        [124]   meta-llama/llama-4-scout
        [125]   meta-llama/llama-guard-3-8b
        [126]   meta-llama/llama-guard-4-12b
        [127]   microsoft/phi-4
        [128]   microsoft/wizardlm-2-8x22b
        [129]   minimax/minimax-01
        [130]   minimax/minimax-m1
        [131]   minimax/minimax-m2
        [132]   minimax/minimax-m2-her
        [133]   minimax/minimax-m2.1
        [134]   minimax/minimax-m2.5
        [135]   minimax/minimax-m2.5:free
        [136]   minimax/minimax-m2.7
        [137]   mistralai/codestral-2508
        [138]   mistralai/devstral-2512
        [139]   mistralai/devstral-medium
        [140]   mistralai/devstral-small
        [141]   mistralai/ministral-14b-2512
        [142]   mistralai/ministral-3b-2512
        [143]   mistralai/ministral-8b-2512
        [144]   mistralai/mistral-7b-instruct-v0.1
        [145]   mistralai/mistral-large
        [146]   mistralai/mistral-large-2407
        [147]   mistralai/mistral-large-2411
        [148]   mistralai/mistral-large-2512
        [149]   mistralai/mistral-medium-3
        [150]   mistralai/mistral-medium-3.1
        [151]   mistralai/mistral-nemo
        [152]   mistralai/mistral-saba
        [153]   mistralai/mistral-small-24b-instruct-2501
        [154]   mistralai/mistral-small-2603
        [155]   mistralai/mistral-small-3.1-24b-instruct
        [156]   mistralai/mistral-small-3.2-24b-instruct
        [157]   mistralai/mistral-small-creative
        [158]   mistralai/mixtral-8x22b-instruct
        [159]   mistralai/mixtral-8x7b-instruct
        [160]   mistralai/pixtral-large-2411
        [161]   mistralai/voxtral-small-24b-2507
        [162]   moonshotai/kimi-k2
        [163]   moonshotai/kimi-k2-0905
        [164]   moonshotai/kimi-k2-thinking
        [165]   moonshotai/kimi-k2.5
        [166]   morph/morph-v3-fast
        [167]   morph/morph-v3-large
        [168]   nex-agi/deepseek-v3.1-nex-n1
        [169]   nousresearch/hermes-2-pro-llama-3-8b
        [170]   nousresearch/hermes-3-llama-3.1-405b
        [171]   nousresearch/hermes-3-llama-3.1-405b:free
        [172]   nousresearch/hermes-3-llama-3.1-70b
        [173]   nousresearch/hermes-4-405b
        [174]   nousresearch/hermes-4-70b
        [175]   nvidia/llama-3.1-nemotron-70b-instruct
        [176]   nvidia/llama-3.1-nemotron-ultra-253b-v1
        [177]   nvidia/llama-3.3-nemotron-super-49b-v1.5
        [178]   nvidia/nemotron-3-nano-30b-a3b
        [179]   nvidia/nemotron-3-nano-30b-a3b:free
        [180]   nvidia/nemotron-3-super-120b-a12b
        [181]   nvidia/nemotron-3-super-120b-a12b:free
        [182]   nvidia/nemotron-nano-12b-v2-vl
        [183]   nvidia/nemotron-nano-12b-v2-vl:free
        [184]   nvidia/nemotron-nano-9b-v2
        [185]   nvidia/nemotron-nano-9b-v2:free
        [186]   openai/gpt-3.5-turbo
        [187]   openai/gpt-3.5-turbo-0613
        [188]   openai/gpt-3.5-turbo-16k
        [189]   openai/gpt-3.5-turbo-instruct
        [190]   openai/gpt-4
        [191]   openai/gpt-4-0314
        [192]   openai/gpt-4-1106-preview
        [193]   openai/gpt-4-turbo
        [194]   openai/gpt-4-turbo-preview
        [195]   openai/gpt-4.1
        [196]   openai/gpt-4.1-mini
        [197]   openai/gpt-4.1-nano
        [198]   openai/gpt-4o
        [199]   openai/gpt-4o-2024-05-13
        [200]   openai/gpt-4o-2024-08-06
        [201]   openai/gpt-4o-2024-11-20
        [202]   openai/gpt-4o-audio-preview
        [203]   openai/gpt-4o-mini
        [204]   openai/gpt-4o-mini-2024-07-18
        [205]   openai/gpt-4o-mini-search-preview
        [206]   openai/gpt-4o-search-preview
        [207]   openai/gpt-4o:extended
        [208]   openai/gpt-5
        [209]   openai/gpt-5-chat
        [210]   openai/gpt-5-codex
        [211]   openai/gpt-5-image
        [212]   openai/gpt-5-image-mini
        [213]   openai/gpt-5-mini
        [214]   openai/gpt-5-nano
        [215]   openai/gpt-5-pro
        [216]   openai/gpt-5.1
        [217]   openai/gpt-5.1-chat
        [218]   openai/gpt-5.1-codex
        [219]   openai/gpt-5.1-codex-max
        [220]   openai/gpt-5.1-codex-mini
        [221]   openai/gpt-5.2
        [222]   openai/gpt-5.2-chat
        [223]   openai/gpt-5.2-codex
        [224]   openai/gpt-5.2-pro
        [225]   openai/gpt-5.3-chat
        [226]   openai/gpt-5.3-codex
        [227]   openai/gpt-5.4
        [228]   openai/gpt-5.4-mini
        [229]   openai/gpt-5.4-nano
        [230]   openai/gpt-5.4-pro
        [231]   openai/gpt-audio
        [232]   openai/gpt-audio-mini
        [233]   openai/gpt-oss-120b
        [234]   openai/gpt-oss-120b:free
        [235]   openai/gpt-oss-20b
        [236]   openai/gpt-oss-20b:free
        [237]   openai/gpt-oss-safeguard-20b
        [238]   openai/o1
        [239]   openai/o1-pro
        [240]   openai/o3
        [241]   openai/o3-deep-research
        [242]   openai/o3-mini
        [243]   openai/o3-mini-high
        [244]   openai/o3-pro
        [245]   openai/o4-mini
        [246]   openai/o4-mini-deep-research
        [247]   openai/o4-mini-high
        [248]   openrouter/auto
        [249]   openrouter/bodybuilder
        [250]   openrouter/free
        [251]   perplexity/sonar
        [252]   perplexity/sonar-deep-research
        [253]   perplexity/sonar-pro
        [254]   perplexity/sonar-pro-search
        [255]   perplexity/sonar-reasoning-pro
        [256]   prime-intellect/intellect-3
        [257]   qwen/qwen-2.5-72b-instruct
        [258]   qwen/qwen-2.5-7b-instruct
        [259]   qwen/qwen-2.5-coder-32b-instruct
        [260]   qwen/qwen-max
        [261]   qwen/qwen-plus
        [262]   qwen/qwen-plus-2025-07-28
        [263]   qwen/qwen-plus-2025-07-28:thinking
        [264]   qwen/qwen-turbo
        [265]   qwen/qwen-vl-max
        [266]   qwen/qwen-vl-plus
        [267]   qwen/qwen2.5-coder-7b-instruct
        [268]   qwen/qwen2.5-vl-32b-instruct
        [269]   qwen/qwen2.5-vl-72b-instruct
        [270]   qwen/qwen3-14b
        [271]   qwen/qwen3-235b-a22b
        [272]   qwen/qwen3-235b-a22b-2507
        [273]   qwen/qwen3-235b-a22b-thinking-2507
        [274]   qwen/qwen3-30b-a3b
        [275]   qwen/qwen3-30b-a3b-instruct-2507
        [276]   qwen/qwen3-30b-a3b-thinking-2507
        [277]   qwen/qwen3-32b
        [278]   qwen/qwen3-8b
        [279]   qwen/qwen3-coder
        [280]   qwen/qwen3-coder-30b-a3b-instruct
        [281]   qwen/qwen3-coder-flash
        [282]   qwen/qwen3-coder-next
        [283]   qwen/qwen3-coder-plus
        [284]   qwen/qwen3-coder:free
        [285]   qwen/qwen3-max
        [286]   qwen/qwen3-max-thinking
        [287]   qwen/qwen3-next-80b-a3b-instruct
        [288]   qwen/qwen3-next-80b-a3b-instruct:free
        [289]   qwen/qwen3-next-80b-a3b-thinking
        [290]   qwen/qwen3-vl-235b-a22b-instruct
        [291]   qwen/qwen3-vl-235b-a22b-thinking
        [292]   qwen/qwen3-vl-30b-a3b-instruct
        [293]   qwen/qwen3-vl-30b-a3b-thinking
        [294]   qwen/qwen3-vl-32b-instruct
        [295]   qwen/qwen3-vl-8b-instruct
        [296]   qwen/qwen3-vl-8b-thinking
        [297]   qwen/qwen3.5-122b-a10b
        [298]   qwen/qwen3.5-27b
        [299]   qwen/qwen3.5-35b-a3b
        [300]   qwen/qwen3.5-397b-a17b
        [301]   qwen/qwen3.5-9b
        [302]   qwen/qwen3.5-flash-02-23
        [303]   qwen/qwen3.5-plus-02-15
        [304]   qwen/qwen3.6-plus
        [305]   qwen/qwq-32b
        [306]   rekaai/reka-edge
        [307]   rekaai/reka-flash-3
        [308]   relace/relace-apply-3
        [309]   relace/relace-search
        [310]   sao10k/l3-euryale-70b
        [311]   sao10k/l3-lunaris-8b
        [312]   sao10k/l3.1-70b-hanami-x1
        [313]   sao10k/l3.1-euryale-70b
        [314]   sao10k/l3.3-euryale-70b
        [315]   stepfun/step-3.5-flash
        [316]   switchpoint/router
        [317]   tencent/hunyuan-a13b-instruct
        [318]   thedrummer/cydonia-24b-v4.1
        [319]   thedrummer/rocinante-12b
        [320]   thedrummer/skyfall-36b-v2
        [321]   thedrummer/unslopnemo-12b
        [322]   tngtech/deepseek-r1t2-chimera
        [323]   undi95/remm-slerp-l2-13b
        [324]   upstage/solar-pro-3
        [325]   writer/palmyra-x5
        [326]   x-ai/grok-3
        [327]   x-ai/grok-3-beta
        [328]   x-ai/grok-3-mini
        [329]   x-ai/grok-3-mini-beta
        [330]   x-ai/grok-4
        [331]   x-ai/grok-4-fast
        [332]   x-ai/grok-4.1-fast
        [333]   x-ai/grok-4.20
        [334]   x-ai/grok-4.20-multi-agent
        [335]   x-ai/grok-code-fast-1
        [336]   xiaomi/mimo-v2-flash
        [337]   xiaomi/mimo-v2-omni
        [338]   xiaomi/mimo-v2-pro
        [339]   z-ai/glm-4-32b
        [340]   z-ai/glm-4.5
        [341]   z-ai/glm-4.5-air
        [342]   z-ai/glm-4.5-air:free
        [343]   z-ai/glm-4.5v
        [344]   z-ai/glm-4.6
        [345]   z-ai/glm-4.6v
        [346]   z-ai/glm-4.7
        [347]   z-ai/glm-4.7-flash
        [348]   z-ai/glm-5
        [349]   z-ai/glm-5-turbo
        [350]   z-ai/glm-5.1
        [351]   z-ai/glm-5v-turbo

[Default]

Enter the index or the name of your default model (leave empty to skip):130

Enter model context length (leave empty to skip):

DEFAULT_VENDOR: OpenRouter
DEFAULT_MODEL: minimax/minimax-m1

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✅ Setup complete! You can now use Fabric.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Next steps:
  • List available patterns: fabric -l
  • Try a pattern: echo 'your text' | fabric --pattern summarize
  • Configure more settings: fabric --setup


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Configuration Status:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ✓ AI Provider configured
  ✓ Default vendor/model set: OpenRouter/minimax/minimax-m1
  ✓ Patterns downloaded
  ✓ Strategies downloaded
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

✓ All required components configured!

root@dockerhost:~# ls
cloudflared-config
root@dockerhost:~# cd .fabric-config/
root@dockerhost:~/.fabric-config# ls
contexts  extensions  locales  patterns  sessions  strategies  unique_patterns.txt
root@dockerhost:~/.fabric-config# cls -l
-bash: cls: command not found
root@dockerhost:~/.fabric-config# ls -lh
total 40K
drwxr-xr-x   2 root root 4.0K Apr  9 11:54 contexts
drwxr-xr-x   2 root root 4.0K Apr  9 11:54 extensions
drwxr-xr-x   2 root root 4.0K Apr  9 11:54 locales
drwx------ 253 root root  12K Apr  9 11:55 patterns
drwxr-xr-x   2 root root 4.0K Apr  9 11:54 sessions
drwxr-xr-x   2 root root 4.0K Apr  9 11:55 strategies
-rw-r--r--   1 root root 5.0K Apr  9 11:55 unique_patterns.txt
root@dockerhost:~/.fabric-config# cat 
contexts/            extensions/          patterns/            strategies/
.env                 locales/             sessions/            unique_patterns.txt
root@dockerhost:~/.fabric-config# cat .env 
DEFAULT_VENDOR=OpenRouter
DEFAULT_MODEL=minimax/minimax-m1
PATTERNS_LOADER_GIT_REPO_URL=https://github.com/danielmiessler/fabric.git
PATTERNS_LOADER_GIT_REPO_PATTERNS_FOLDER=data/patterns
PROMPT_STRATEGIES_GIT_REPO_URL=https://github.com/danielmiessler/fabric.git
PROMPT_STRATEGIES_GIT_REPO_STRATEGIES_FOLDER=data/strategies
OPENROUTER_API_KEY=123456
OPENROUTER_API_BASE_URL=https://openrouter.ai/api/v1
root@dockerhost:~/.fabric-config# 

```


