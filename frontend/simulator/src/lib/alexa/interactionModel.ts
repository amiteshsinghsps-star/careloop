/* GENERATED — do not edit. Source: alexa/interaction-model.json (sha256 ec4b62d616b3f4b62f706c031e7738f37f4533bd86b79beb3dc8752af9330a9d)
 * Regenerate with `npm run model:gen`; drift is checked by `npm run model:check`.
 * 13 intents, 5 slot types. The simulated device resolves utterances,
 * sets its silence/reprompt timers and arms its one device-initiated reminder from this artifact, so
 * what the demo does is what the skill ships.
 *
 * Health text never originates here: every string in `promptCatalog` is a closed-vocabulary
 * constant authored in `scripts/make_addon.py`, and a care answer always comes from the server.
 */
export const INTERACTION_MODEL = {
  "schemaVersion": "careloop-interaction-model/1",
  "languageModel": {
    "intents": [
      {
        "answerGuidance": {
          "maxSpokenSeconds": 30,
          "oneItemThenCard": true,
          "say": "Name the person, say the most immediate item in the clinician's own words, then say how many more there are and offer them on screen."
        },
        "name": "WhatsMyPlanIntent",
        "response": {
          "directives": [
            {
              "resourceUri": "ui://careloop/card",
              "type": "render-mcp-app-card"
            }
          ]
        },
        "routing": {
          "slotArguments": {
            "Person": "person",
            "Section": "kind"
          },
          "surface": "voice",
          "tool": "get_care_plan",
          "type": "mcp-tool"
        },
        "samples": [
          "what is next",
          "what is next for {Person}",
          "what is the plan for {Person}",
          "what did the clinician say for {Person}",
          "what should {Person} do now",
          "what has {Person} still got to do",
          "list the {Section} items for {Person}",
          "what was I told to do for {Person}",
          "show me the plan for {Person}",
          "what is still pending for {Person}"
        ],
        "slots": [
          {
            "name": "Person",
            "prompt": "ClarifyPersonPrompt",
            "required": false,
            "type": "CareLoopPerson"
          },
          {
            "name": "Section",
            "required": false,
            "type": "CareLoopPlanSection"
          }
        ]
      },
      {
        "answerGuidance": {
          "maxSpokenSeconds": 30,
          "oneItemThenCard": true,
          "say": "Give the earliest part of the day with its first item, then say how many other times of day have something left."
        },
        "name": "TodaysPlanIntent",
        "response": {
          "directives": [
            {
              "resourceUri": "ui://careloop/card",
              "type": "render-mcp-app-card"
            }
          ]
        },
        "routing": {
          "slotArguments": {
            "Person": "person"
          },
          "surface": "voice",
          "tool": "todays_plan",
          "type": "mcp-tool"
        },
        "samples": [
          "what is on today",
          "what is the plan for today",
          "what is on today for {Person}",
          "what does {Person} have today",
          "today's day for {Person}",
          "what happens this morning",
          "what is there for {Person} tonight",
          "walk me through today for {Person}"
        ],
        "slots": [
          {
            "name": "Person",
            "prompt": "ClarifyPersonPrompt",
            "required": false,
            "type": "CareLoopPerson"
          }
        ]
      },
      {
        "answerGuidance": {
          "maxSpokenSeconds": 30,
          "oneItemThenCard": true,
          "say": "Say it is recorded, then say how much is still pending."
        },
        "name": "MarkDoneIntent",
        "response": {
          "directives": []
        },
        "routing": {
          "slotArguments": {
            "ItemPosition": "ref",
            "Person": "who"
          },
          "surface": "voice",
          "tool": "mark_done",
          "type": "mcp-tool"
        },
        "samples": [
          "mark the {ItemPosition} one as done",
          "the {ItemPosition} one is done",
          "{Person} finished the {ItemPosition} item",
          "tick off the {ItemPosition} thing",
          "record the {ItemPosition} item as finished",
          "that one is sorted",
          "the {ItemPosition} thing has been dealt with for {Person}"
        ],
        "slots": [
          {
            "name": "ItemPosition",
            "prompt": "ElicitItemPrompt",
            "required": true,
            "type": "CareLoopItemPosition"
          },
          {
            "name": "Person",
            "prompt": "ClarifyPersonPrompt",
            "required": false,
            "type": "CareLoopPerson"
          }
        ]
      },
      {
        "answerGuidance": {
          "maxSpokenSeconds": 30,
          "oneItemThenCard": true,
          "say": "Read back the clinician's own recorded words for that item and the date they were recorded. Do not supply a reason the recording does not contain: if it does not say why, say that it does not say."
        },
        "name": "ExplainInstructionIntent",
        "response": {
          "directives": []
        },
        "routing": {
          "slotArguments": {
            "ItemPosition": "ref"
          },
          "surface": "voice",
          "tool": "explain_instruction",
          "type": "mcp-tool"
        },
        "samples": [
          "why did the clinician say that",
          "why is the {ItemPosition} item there",
          "explain the {ItemPosition} one",
          "what were the exact words for the {ItemPosition} item",
          "when was that recorded",
          "where did that come from",
          "read back the {ItemPosition} instruction"
        ],
        "slots": [
          {
            "name": "ItemPosition",
            "prompt": "ElicitItemPrompt",
            "required": true,
            "type": "CareLoopItemPosition"
          }
        ]
      },
      {
        "answerGuidance": {
          "maxSpokenSeconds": 30,
          "oneItemThenCard": true,
          "say": "Repeat the sentence back exactly as stored and say it now travels with the plan."
        },
        "name": "AddVerbalNoteIntent",
        "preChecks": [
          "crisis",
          "child-safety",
          "prompt-injection"
        ],
        "response": {
          "directives": []
        },
        "routing": {
          "slotArguments": {
            "NoteText": "text",
            "Person": "person"
          },
          "surface": "voice",
          "tool": "add_note",
          "type": "mcp-tool"
        },
        "samples": [
          "remember that {NoteText}",
          "make a note that {NoteText}",
          "add a note for {Person}",
          "{Person} was told {NoteText}",
          "keep this with the plan",
          "write down {NoteText}"
        ],
        "slots": [
          {
            "capture": "listenOnce",
            "name": "NoteText",
            "prompt": "ElicitNotePrompt",
            "required": true,
            "type": "CareLoopNoteText",
            "wildcard": true
          },
          {
            "name": "Person",
            "prompt": "ClarifyPersonPrompt",
            "required": false,
            "type": "CareLoopPerson"
          }
        ]
      },
      {
        "answerGuidance": {
          "maxSpokenSeconds": 30,
          "oneItemThenCard": true,
          "say": "Say the names the household plans cover."
        },
        "name": "ListPeopleIntent",
        "response": {
          "directives": [
            {
              "resourceUri": "ui://careloop/card",
              "type": "render-mcp-app-card"
            }
          ]
        },
        "routing": {
          "slotArguments": {},
          "surface": "voice",
          "tool": "list_people",
          "type": "mcp-tool"
        },
        "samples": [
          "who is in my household",
          "who do you have plans for",
          "list the people in the plan",
          "whose plan are you looking after",
          "who can I ask about"
        ],
        "slots": []
      },
      {
        "answerGuidance": {
          "maxSpokenSeconds": 30,
          "oneItemThenCard": true,
          "say": "Give the counts of new, stopped and changed items and the visit they were compared with."
        },
        "name": "WhatChangedIntent",
        "response": {
          "directives": []
        },
        "routing": {
          "slotArguments": {
            "Person": "person"
          },
          "surface": "voice",
          "tool": "what_changed",
          "type": "mcp-tool"
        },
        "samples": [
          "what changed since the last visit",
          "what is new since the last appointment",
          "what changed for {Person}",
          "did anything stop since last time",
          "compare this with the previous visit for {Person}"
        ],
        "slots": [
          {
            "name": "Person",
            "prompt": "ClarifyPersonPrompt",
            "required": false,
            "type": "CareLoopPerson"
          }
        ]
      },
      {
        "answerGuidance": {
          "maxSpokenSeconds": 30,
          "oneItemThenCard": true,
          "say": "Say how much of today is done and how much is still pending, in one sentence."
        },
        "name": "DailyDigestIntent",
        "response": {
          "directives": []
        },
        "routing": {
          "slotArguments": {
            "Person": "person"
          },
          "surface": "voice",
          "tool": "daily_digest",
          "type": "mcp-tool"
        },
        "samples": [
          "how am I doing today",
          "how much of the plan is done",
          "what is still left today",
          "what has {Person} got left today",
          "am I on track for {Person}"
        ],
        "slots": [
          {
            "name": "Person",
            "prompt": "ClarifyPersonPrompt",
            "required": false,
            "type": "CareLoopPerson"
          }
        ]
      },
      {
        "answerGuidance": {
          "maxSpokenSeconds": 30,
          "oneItemThenCard": true,
          "say": "Read out the items still waiting on the clinician, and say they are only questions to ask."
        },
        "name": "PrepareVisitQuestionsIntent",
        "response": {
          "directives": []
        },
        "routing": {
          "slotArguments": {
            "Person": "person"
          },
          "surface": "voice",
          "tool": "prepare_next_visit",
          "type": "mcp-tool"
        },
        "samples": [
          "what should I ask at the next visit",
          "prepare questions for the next appointment",
          "what do I need to check with the clinician for {Person}",
          "what is still flagged for {Person}",
          "make me a list for the next visit"
        ],
        "slots": [
          {
            "name": "Person",
            "prompt": "ClarifyPersonPrompt",
            "required": false,
            "type": "CareLoopPerson"
          }
        ]
      },
      {
        "answerGuidance": {
          "maxSpokenSeconds": 30,
          "oneItemThenCard": true,
          "say": "Read the item back and ask for a clear yes before anything goes. Never remove on the first request."
        },
        "name": "RemoveInstructionIntent",
        "response": {
          "directives": []
        },
        "routing": {
          "confirmArgument": "confirm",
          "slotArguments": {
            "ItemPosition": "ref"
          },
          "surface": "voice",
          "tool": "delete_instruction",
          "type": "mcp-tool"
        },
        "samples": [
          "delete the {ItemPosition} item",
          "remove the {ItemPosition} one from the plan",
          "take the {ItemPosition} instruction away for {Person}",
          "cross out the {ItemPosition} thing",
          "that instruction is wrong, remove it"
        ],
        "slots": [
          {
            "name": "ItemPosition",
            "prompt": "ElicitItemPrompt",
            "required": true,
            "type": "CareLoopItemPosition"
          },
          {
            "name": "Person",
            "prompt": "ClarifyPersonPrompt",
            "required": false,
            "type": "CareLoopPerson"
          }
        ]
      },
      {
        "answerGuidance": {
          "maxSpokenSeconds": 30,
          "oneItemThenCard": true,
          "say": "Say what CareLoop can help with in one or two sentences, and that it only repeats what a clinician recorded."
        },
        "answerWhenUnlinked": "LinkHouseholdPrompt",
        "name": "AboutCareLoopIntent",
        "response": {
          "directives": []
        },
        "routing": {
          "slotArguments": {},
          "surface": "voice",
          "tool": "about_careloop",
          "type": "mcp-tool"
        },
        "samples": [
          "what can you do",
          "what are you for",
          "how does this work",
          "what can CareLoop help with",
          "what do you know about"
        ],
        "slots": []
      },
      {
        "answerGuidance": {
          "maxSpokenSeconds": 30,
          "oneItemThenCard": true,
          "say": "Say which way the delivery changed, using the line the artifact names for it, then speak again the answer this customer already had. Never say anything about the plan that the held answer did not already say."
        },
        "name": "AdjustDeliveryIntent",
        "response": {
          "directives": []
        },
        "routing": {
          "surface": "voice",
          "tool": null,
          "type": "device-local",
          "writesAnything": false
        },
        "samples": [
          "say that {Delivery}",
          "say it {Delivery}",
          "speak {Delivery}",
          "read it back {Delivery}",
          "can you say that {Delivery}",
          "could you be {Delivery}"
        ],
        "slots": [
          {
            "name": "Delivery",
            "prompt": "ElicitDeliveryPrompt",
            "required": true,
            "type": "CareLoopDeliveryStyle"
          }
        ]
      },
      {
        "answerGuidance": {
          "maxSpokenSeconds": 30,
          "oneItemThenCard": true,
          "say": "Say that the device will answer as that household member for the rest of this session and that what may be read out is bounded by what the household lets that member hear. Name no item, no amount and no flag from any plan."
        },
        "name": "IdentifySpeakerIntent",
        "response": {
          "directives": []
        },
        "routing": {
          "surface": "voice",
          "tool": null,
          "type": "device-local",
          "writesAnything": false
        },
        "samples": [
          "this is {Person}",
          "I am {Person}",
          "speaking as {Person}",
          "answer as {Person}",
          "switch to {Person}",
          "you are talking to {Person}"
        ],
        "slots": [
          {
            "name": "Person",
            "prompt": "ClarifyPersonPrompt",
            "required": true,
            "type": "CareLoopPerson"
          }
        ]
      }
    ],
    "invocationName": "care loop",
    "modelDescription": "Voice surface for one household's recorded adult care plan. Dose-free by design: no intent collects or returns a medicine amount, and nothing here suggests a medicine, a change or a conclusion.",
    "types": [
      {
        "name": "CareLoopPerson",
        "note": "Who the plan is about, said the way families say it. Resolved against the household's own people; an unclear match comes back as a question, never a guess.",
        "resolution": "household aliases",
        "values": [
          {
            "id": "mother",
            "name": {
              "value": "mother"
            }
          },
          {
            "id": "mum",
            "name": {
              "value": "mum"
            }
          },
          {
            "id": "mom",
            "name": {
              "value": "mom"
            }
          },
          {
            "id": "my wife",
            "name": {
              "value": "my wife"
            }
          },
          {
            "id": "my mum",
            "name": {
              "value": "my mum"
            }
          },
          {
            "id": "father",
            "name": {
              "value": "father"
            }
          },
          {
            "id": "dad",
            "name": {
              "value": "dad"
            }
          },
          {
            "id": "papa",
            "name": {
              "value": "papa"
            }
          },
          {
            "id": "my husband",
            "name": {
              "value": "my husband"
            }
          },
          {
            "id": "grandmother",
            "name": {
              "value": "grandmother"
            }
          },
          {
            "id": "grandfather",
            "name": {
              "value": "grandfather"
            }
          },
          {
            "id": "nan",
            "name": {
              "value": "nan"
            }
          },
          {
            "id": "grandma",
            "name": {
              "value": "grandma"
            }
          },
          {
            "id": "grandad",
            "name": {
              "value": "grandad"
            }
          },
          {
            "id": "aunt",
            "name": {
              "value": "aunt"
            }
          },
          {
            "id": "uncle",
            "name": {
              "value": "uncle"
            }
          },
          {
            "id": "my mother in law",
            "name": {
              "value": "my mother in law"
            }
          },
          {
            "id": "my father in law",
            "name": {
              "value": "my father in law"
            }
          },
          {
            "id": "my partner",
            "name": {
              "value": "my partner"
            }
          },
          {
            "id": "my sister",
            "name": {
              "value": "my sister"
            }
          },
          {
            "id": "my brother",
            "name": {
              "value": "my brother"
            }
          },
          {
            "id": "the nurse",
            "name": {
              "value": "the nurse"
            }
          },
          {
            "id": "household member",
            "name": {
              "value": "household member"
            }
          }
        ]
      },
      {
        "name": "CareLoopItemPosition",
        "note": "Which item from the plan just read out, by its place in the list. Only places the server resolves are offered, so no item identifier is ever spoken or collected.",
        "resolution": "ordinal words the server can resolve",
        "values": [
          {
            "id": "first",
            "name": {
              "value": "first"
            }
          },
          {
            "id": "second",
            "name": {
              "value": "second"
            }
          },
          {
            "id": "third",
            "name": {
              "value": "third"
            }
          },
          {
            "id": "fourth",
            "name": {
              "value": "fourth"
            }
          },
          {
            "id": "fifth",
            "name": {
              "value": "fifth"
            }
          }
        ]
      },
      {
        "name": "CareLoopPlanSection",
        "note": "A part of the plan. The value is what a family says and the id is the kind the server's own contract names, so a synonym can never be sent as an argument the tool would reject.",
        "resolution": "closed kind vocabulary",
        "values": [
          {
            "id": "medication",
            "name": {
              "value": "medicine"
            }
          },
          {
            "id": "medication",
            "name": {
              "value": "tablet"
            }
          },
          {
            "id": "medication",
            "name": {
              "value": "pill"
            }
          },
          {
            "id": "task",
            "name": {
              "value": "task"
            }
          },
          {
            "id": "task",
            "name": {
              "value": "thing to do"
            }
          },
          {
            "id": "avoid",
            "name": {
              "value": "avoid"
            }
          },
          {
            "id": "avoid",
            "name": {
              "value": "not to do"
            }
          },
          {
            "id": "diet",
            "name": {
              "value": "food"
            }
          },
          {
            "id": "diet",
            "name": {
              "value": "diet"
            }
          },
          {
            "id": "monitor",
            "name": {
              "value": "check up"
            }
          },
          {
            "id": "monitor",
            "name": {
              "value": "blood pressure"
            }
          },
          {
            "id": "monitor",
            "name": {
              "value": "reading"
            }
          },
          {
            "id": "appointment",
            "name": {
              "value": "visit"
            }
          },
          {
            "id": "appointment",
            "name": {
              "value": "appointment"
            }
          }
        ]
      },
      {
        "name": "CareLoopNoteText",
        "note": "The caller's own words for something the family was told verbally. CareLoop stores them as spoken and adds nothing of its own.",
        "resolution": "free text, captured once",
        "values": [],
        "wildcard": true
      },
      {
        "name": "CareLoopDeliveryStyle",
        "note": "How an answer is spoken, never what it says. Each value is a style id that `presentation.delivery.styles` maps to a rate, a volume and a word ceiling, so the device reads the numbers from this artifact instead of hard-coding them, and a tighter answer is still the same sentence cut to length rather than a new sentence.",
        "resolution": "closed delivery vocabulary",
        "values": [
          {
            "id": "slower",
            "name": {
              "value": "slower"
            }
          },
          {
            "id": "slower",
            "name": {
              "value": "more slowly"
            }
          },
          {
            "id": "slower",
            "name": {
              "value": "at a slower pace"
            }
          },
          {
            "id": "slower",
            "name": {
              "value": "say that slow"
            }
          },
          {
            "id": "loud",
            "name": {
              "value": "louder"
            }
          },
          {
            "id": "loud",
            "name": {
              "value": "more loudly"
            }
          },
          {
            "id": "loud",
            "name": {
              "value": "speak up"
            }
          },
          {
            "id": "soft",
            "name": {
              "value": "softer"
            }
          },
          {
            "id": "soft",
            "name": {
              "value": "quieter"
            }
          },
          {
            "id": "soft",
            "name": {
              "value": "in a low voice"
            }
          },
          {
            "id": "brief",
            "name": {
              "value": "keep it short"
            }
          },
          {
            "id": "brief",
            "name": {
              "value": "the short version"
            }
          },
          {
            "id": "brief",
            "name": {
              "value": "just the essentials"
            }
          },
          {
            "id": "brief",
            "name": {
              "value": "shorter"
            }
          },
          {
            "id": "normal",
            "name": {
              "value": "the usual way"
            }
          },
          {
            "id": "normal",
            "name": {
              "value": "normally"
            }
          },
          {
            "id": "normal",
            "name": {
              "value": "back to normal"
            }
          }
        ]
      }
    ]
  },
  "dialog": {
    "confirmationRequired": {
      "RemoveInstructionIntent": {
        "defaultValue": "ON",
        "prompt": "ConfirmRemovePrompt",
        "type": "PLAIN"
      }
    },
    "confirmationResponses": {
      "RemoveInstructionIntent": {
        "accept": {
          "renderPrompt": "RemovedPrompt",
          "toolConfirm": true
        },
        "reject": {
          "renderPrompt": "KeptPrompt",
          "toolConfirm": false
        }
      }
    },
    "delegation": {
      "strategy": {
        "type": "skill"
      }
    },
    "eloquenceStyle": "concise"
  },
  "proactive": {
    "reminder": {
      "causeType": "ConnectionCompleted",
      "connectionUri": "connection://CareLoop.CareReminder/1",
      "directiveType": "Connections.StartConnection",
      "intent": "TodaysPlanIntent",
      "onCompletion": "RESUME_SESSION",
      "prompt": "ReminderPrompt",
      "requestType": "SessionResumedRequest",
      "resumeAfterSeconds": 8,
      "writesAnything": false
    }
  },
  "presentation": {
    "delivery": {
      "appliesToHeldAnswer": true,
      "defaultStyle": "normal",
      "intent": "AdjustDeliveryIntent",
      "slot": "Delivery",
      "styles": {
        "brief": {
          "maxSpokenWords": 18,
          "prompt": "DeliveryBriefPrompt",
          "rate": "100%",
          "volume": null
        },
        "loud": {
          "maxSpokenWords": 48,
          "prompt": "DeliveryLoudPrompt",
          "rate": "100%",
          "volume": "140%"
        },
        "normal": {
          "maxSpokenWords": 48,
          "prompt": "DeliveryNormalPrompt",
          "rate": "100%",
          "volume": "100%"
        },
        "slower": {
          "maxSpokenWords": 48,
          "prompt": "DeliverySlowerPrompt",
          "rate": "80%",
          "volume": null
        },
        "soft": {
          "maxSpokenWords": 48,
          "prompt": "DeliverySoftPrompt",
          "rate": "100%",
          "volume": "60%"
        }
      },
      "writesAnything": false
    }
  },
  "voiceProfile": {
    "assertion": {
      "deviceDecidesScope": false,
      "intent": "IdentifySpeakerIntent",
      "profileMetaKey": "careloop/voiceProfile",
      "profileValue": "careloop-simulated/1",
      "prompt": "SpeakerTakenPrompt",
      "sessionScopedOnly": true,
      "simulated": true,
      "slot": "Person",
      "speakerMetaKey": "careloop/speaker",
      "writesAnything": false
    }
  },
  "promptCatalog": {
    "note": "Prompts are plain sentences for speaking. They never name an internal field, a code, an identifier or a technical format, and every one fits inside maxSpokenSeconds.",
    "prompts": [
      {
        "body": [
          "CareLoop is ready. Ask what is next for someone, why an instruction was given, or say what you want remembered."
        ],
        "id": "WelcomePrompt",
        "maxSpokenSeconds": 30,
        "type": "PlainText"
      },
      {
        "body": [
          "I only repeat what your clinician recorded for this household. I can tell you what is next, why something was said, what changed, and what is still to do."
        ],
        "id": "HelpPrompt",
        "maxSpokenSeconds": 30,
        "type": "PlainText"
      },
      {
        "body": [
          "I didn't catch that. You can ask what is next for someone, or what changed since the last visit."
        ],
        "id": "FallbackPrompt",
        "maxSpokenSeconds": 30,
        "type": "PlainText"
      },
      {
        "body": [
          "Who should I ask about? Say the name or the relation, like my mother."
        ],
        "id": "ClarifyPersonPrompt",
        "maxSpokenSeconds": 30,
        "type": "PlainText"
      },
      {
        "body": [
          "Which one on the list? Say the first, the second, the third, the fourth or the fifth."
        ],
        "id": "ElicitItemPrompt",
        "maxSpokenSeconds": 30,
        "type": "PlainText"
      },
      {
        "body": [
          "What should I remember? Say the sentence in your own words and I keep it exactly as you say it."
        ],
        "id": "ElicitNotePrompt",
        "maxSpokenSeconds": 30,
        "type": "PlainText"
      },
      {
        "body": [
          "Link your CareLoop household account to ask about a care plan."
        ],
        "id": "LinkHouseholdPrompt",
        "maxSpokenSeconds": 30,
        "type": "PlainText"
      },
      {
        "body": [
          "I don't have a recording about that. Ask for the plan again, then refer to an item by its place, like the second one."
        ],
        "id": "NoPlanPrompt",
        "maxSpokenSeconds": 30,
        "type": "PlainText"
      },
      {
        "body": [
          "Your household role can't do that by voice. You can do it on the CareLoop companion page."
        ],
        "id": "NotAllowedPrompt",
        "maxSpokenSeconds": 30,
        "type": "PlainText"
      },
      {
        "body": [
          "Just to check. Should I really remove the {ItemPosition} item from this household's plan? Say yes to remove it, or no to keep it."
        ],
        "id": "ConfirmRemovePrompt",
        "maxSpokenSeconds": 30,
        "type": "PlainText"
      },
      {
        "body": [
          "It has been removed from the plan."
        ],
        "id": "RemovedPrompt",
        "maxSpokenSeconds": 30,
        "type": "PlainText"
      },
      {
        "body": [
          "Nothing was removed. It stays in the plan."
        ],
        "id": "KeptPrompt",
        "maxSpokenSeconds": 30,
        "type": "PlainText"
      },
      {
        "body": [
          "CareLoop is reading today's plan back to you."
        ],
        "id": "ReminderPrompt",
        "maxSpokenSeconds": 30,
        "type": "PlainText"
      },
      {
        "body": [
          "How should I say it? You can ask for it slower, louder, softer, shorter or the usual way."
        ],
        "id": "ElicitDeliveryPrompt",
        "maxSpokenSeconds": 30,
        "type": "PlainText"
      },
      {
        "body": [
          "Slower now."
        ],
        "id": "DeliverySlowerPrompt",
        "maxSpokenSeconds": 30,
        "type": "PlainText"
      },
      {
        "body": [
          "Louder now."
        ],
        "id": "DeliveryLoudPrompt",
        "maxSpokenSeconds": 30,
        "type": "PlainText"
      },
      {
        "body": [
          "Softer now."
        ],
        "id": "DeliverySoftPrompt",
        "maxSpokenSeconds": 30,
        "type": "PlainText"
      },
      {
        "body": [
          "The short version."
        ],
        "id": "DeliveryBriefPrompt",
        "maxSpokenSeconds": 30,
        "type": "PlainText"
      },
      {
        "body": [
          "Back to the usual pace and length."
        ],
        "id": "DeliveryNormalPrompt",
        "maxSpokenSeconds": 30,
        "type": "PlainText"
      },
      {
        "body": [
          "For this session I will answer as {Person}. What I can read out is bounded by what your household lets {Person} hear."
        ],
        "id": "SpeakerTakenPrompt",
        "maxSpokenSeconds": 30,
        "type": "PlainText"
      },
      {
        "body": [
          "I don't say amounts out loud. Open the CareLoop companion page for this household to read the exact words your clinician recorded, amounts included."
        ],
        "id": "AmountRedirectPrompt",
        "maxSpokenSeconds": 30,
        "type": "PlainText"
      },
      {
        "body": [
          "If anyone is in danger right now, please call your local emergency number. In India you can call 112, or the Tele-MANAS mental-health helpline 14416 (toll-free, 24x7). I have not saved this as a care item."
        ],
        "id": "CrisisPrompt",
        "maxSpokenSeconds": 30,
        "type": "PlainText"
      }
    ]
  },
  "answerGuidance": {
    "attribution": "Every care answer says it came from your clinician and when it was recorded.",
    "maxSpokenSeconds": 30,
    "maxSpokenWords": 48,
    "plainWords": "Speak the clinician's recorded words rather than a summary of them.",
    "repromptAfterSeconds": 12,
    "rule": "One item out loud, then offer the rest on screen. Long lists go on the card, never into speech.",
    "silenceTimeoutSeconds": 8
  },
  "guardrails": {
    "ambiguousPerson": {
      "handling": "When more than one person could be meant, ask who and then continue; the server returns a short question to speak.",
      "prompt": "ClarifyPersonPrompt"
    },
    "childSafety": {
      "handling": "CareLoop keeps adult care plans. A note about a child is refused with a plain explanation and nothing is stored.",
      "storesAnything": false
    },
    "crisis": {
      "handling": "Checked before anything else, before storage and before any plan answer. If the caller or the person they care for sounds in danger, the assistant reads the emergency redirect, saves nothing and does not continue the plan. No intent is allowed to go past that redirect.",
      "prompt": "CrisisPrompt",
      "spokenWords": "If anyone is in danger right now, please call your local emergency number. In India you can call 112, or the Tele-MANAS mental-health helpline 14416 (toll-free, 24x7). I have not saved this as a care item.",
      "storesAnything": false
    },
    "doseFree": {
      "collectsAnAmount": false,
      "mechanism": "every outbound field is masked, then checked again; a residual amount is an incident that is counted, not a retry",
      "numericAmountSlots": false,
      "redirectPrompt": "AmountRedirectPrompt",
      "statement": "Nothing spoken on this surface may carry a medicine amount. Amounts are replaced with 'your prescribed amount' before an answer is spoken, and the full record is only on the household companion page."
    },
    "neverAsk": [
      "how much of a medicine to take",
      "what someone is suffering from",
      "which medicine to buy, stop or swap",
      "anything about a child",
      "a password, family PIN or household code out loud"
    ],
    "nothingInvented": {
      "noAdviceGiven": true,
      "prompt": "NoPlanPrompt",
      "statement": "The assistant never suggests, changes, stops or explains the reason for an instruction. It repeats what was recorded, and when it has no recording it says so.",
      "unsureIsFlagged": "anything unclear is returned flagged for the clinician instead of smoothed over"
    },
    "promptInjection": {
      "handling": "Text that reads like instructions for a system rather than care for a person is refused on the voice surface.",
      "redirect": "the companion page"
    },
    "roleDenied": {
      "handling": "A household role that can't act by voice says so and points at the companion page.",
      "prompt": "NotAllowedPrompt"
    },
    "speakerScope": {
      "decidedBy": "the linked household's stored boundary",
      "deviceDecides": false,
      "handling": "What may be read out depends on who the household is answering. The linked household stores which member hears whose plan and whether an instruction still waiting on the clinician may be spoken to them; a question outside that boundary is refused with one plain sentence that the service itself returns, and the refusal is recorded in that household's own ledger. A household that declares no boundary is answered exactly as before.",
      "refusalStoresAnything": false
    },
    "unknownItem": {
      "handling": "An item that is no longer on screen is not guessed at; the plan is asked for again.",
      "prompt": "NoPlanPrompt"
    },
    "unlinked": {
      "handling": "Anything about a real plan needs the household linked first.",
      "prompt": "LinkHouseholdPrompt",
      "spokenWords": "Link your CareLoop household account to ask about a care plan."
    }
  },
  "welcomePrompt": "WelcomePrompt",
  "helpPrompt": "HelpPrompt",
  "fallbackPrompt": "FallbackPrompt",
  "intentsNotOnThisSurface": [
    {
      "reason": "no voice intent maps onto it: it is only meaningful where the caregiver can see the whole record on the household companion page",
      "tool": "confirm_instruction"
    }
  ]
} as const;

export type AskIntentName = (typeof INTERACTION_MODEL.languageModel.intents)[number]['name'];
export type AskSlotTypeName = (typeof INTERACTION_MODEL.languageModel.types)[number]['name'];
export type AskPromptId = (typeof INTERACTION_MODEL.promptCatalog.prompts)[number]['id'];

/** The model's own voice budget, in one place, so the runtime cannot quietly loosen it. */
export const VOICE_BUDGET = {
  maxSpokenWords: INTERACTION_MODEL.answerGuidance.maxSpokenWords,
  maxSpokenSeconds: INTERACTION_MODEL.answerGuidance.maxSpokenSeconds,
  silenceTimeoutSeconds: INTERACTION_MODEL.answerGuidance.silenceTimeoutSeconds,
  repromptAfterSeconds: INTERACTION_MODEL.answerGuidance.repromptAfterSeconds,
} as const;

/** The dialog rules, keyed by intent, from `dialog.*` in the model. */
export const DIALOG = INTERACTION_MODEL.dialog as {
  confirmationRequired: Record<string, { defaultValue: string; prompt: string; type: string }>;
  confirmationResponses: Record<string, { accept: { renderPrompt: string; toolConfirm: boolean }; reject: { renderPrompt: string; toolConfirm: boolean } }>;
  delegation: { strategy: { type: string } };
  eloquenceStyle: string;
};

/** The device-initiated reminder, as the artifact declares it: which answer arms the connection,
 *  what the resumed session opens with and how long the device waits before it speaks. Nothing in
 *  the runtime may hard-code any of these. */
export const PROACTIVE = INTERACTION_MODEL.proactive.reminder as {
  causeType: string;
  connectionUri: string;
  directiveType: string;
  intent: AskIntentName;
  onCompletion: string;
  prompt: AskPromptId;
  requestType: string;
  resumeAfterSeconds: number;
  writesAnything: boolean;
};

/** Adaptive spoken delivery, as the artifact declares it. A style is rate, volume and a word
 *  ceiling; the sentence itself is always the one the server returned. The intent that collects a
 *  style routes to no tool, so nothing here can change a plan. */
export const DELIVERY = INTERACTION_MODEL.presentation.delivery as {
  appliesToHeldAnswer: boolean;
  defaultStyle: string;
  intent: AskIntentName;
  slot: string;
  styles: Record<string, { maxSpokenWords: number; prompt: AskPromptId; rate: string; volume: string | null }>;
  writesAnything: false;
};

/** The simulated voice profile: the two MCP `_meta` keys the device sets and the value that
 *  labels the profile as simulated. The device never decides what a member may hear — the server
 *  does, from the boundary the household stored. */
export const VOICE_PROFILE = INTERACTION_MODEL.voiceProfile.assertion as {
  deviceDecidesScope: boolean;
  intent: AskIntentName;
  profileMetaKey: string;
  profileValue: string;
  prompt: AskPromptId;
  sessionScopedOnly: boolean;
  simulated: boolean;
  slot: string;
  speakerMetaKey: string;
  writesAnything: false;
};

export const PROMPTS: Record<AskPromptId, string> = Object.fromEntries(
  INTERACTION_MODEL.promptCatalog.prompts.map((p) => [p.id, p.body[0]]),
) as Record<AskPromptId, string>;

export const MODEL_SHA256 = 'ec4b62d616b3f4b62f706c031e7738f37f4533bd86b79beb3dc8752af9330a9d';
