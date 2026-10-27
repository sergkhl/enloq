"""Wholly authored synthetic group days for ADR 34's citation check and ADR 59's
Ask this group measurements. Nothing comes from a real chat or person."""

# Twelve look-alike bed checks are a deliberate near-duplicate distractor: the
# aphids on bed 7 are reported later, in a separately written message.
_BED_CHECKS = [
    {
        "id": 13 + n,
        "speaker": 1 if n % 2 else 2,
        "minute": 58 + 2 * n,
        "text": f"Bed {n} check: soil is dry at the north end and the label is "
        "still readable. Nothing to report about pests. Water it in the evening "
        "if there is no rain.",
    }
    for n in range(1, 13)
]

GARDEN = {
    "name": "garden",
    "title": "Authored community garden",
    "language": "en",
    "speakers": [
        "Authored Dana",
        "Authored Eli",
        "Authored Marta",
        "Authored Tobias",
        "Authored Noor",
    ],
    "days": [
        {
            "days_ago": 24,
            "messages": [
                {
                    "id": 1,
                    "speaker": 0,
                    "minute": 0,
                    "text": "Morning all! Season planning thread. Plot fees are €35 per plot this year, due by the 15th.",
                },
                {"id": 2, "speaker": 1, "minute": 4, "text": "Morning 🌱"},
                {
                    "id": 3,
                    "speaker": 2,
                    "minute": 6,
                    "text": "Same as last year then? I thought it went up.",
                },
                {
                    "id": 4,
                    "speaker": 0,
                    "minute": 9,
                    "text": "No, same price. The committee kept it.",
                    "reply_to": 3,
                },
                {
                    "id": 5,
                    "speaker": 3,
                    "minute": 15,
                    "text": "I can fix the leaking water butt by the shed, I just need a new tap. Should be under €10.",
                },
                {
                    "id": 6,
                    "speaker": 0,
                    "minute": 18,
                    "text": "Thanks Tobias! The shed key is with me for now, I'll hand it to Eli at the working party.",
                },
                {
                    "id": 7,
                    "speaker": 4,
                    "minute": 25,
                    "text": "Has anyone ordered compost yet?",
                },
                {
                    "id": 8,
                    "speaker": 0,
                    "minute": 27,
                    "text": "Ordering 3 cubic metres from the depot for Saturday at 9. Can someone be there to show the driver where to tip it?",
                    "reply_to": 7,
                },
                {
                    "id": 9,
                    "speaker": 1,
                    "minute": 31,
                    "text": "I can be there Saturday 9.",
                },
                {
                    "id": 10,
                    "speaker": 2,
                    "minute": 40,
                    "text": "Watering rota idea: Eli Mon–Wed, me Thu–Sat. Sunday is the gap. Thoughts?",
                },
                {"id": 11, "speaker": 4, "minute": 44, "text": "+1 for the rota"},
                {
                    "id": 12,
                    "speaker": 3,
                    "minute": 46,
                    "text": "Sunday I'm away most weekends, sorry",
                },
                {
                    "id": 13,
                    "speaker": 0,
                    "minute": 52,
                    "text": "Let's leave Sunday open for now and decide later.",
                },
                *_BED_CHECKS,
                {
                    "id": 26,
                    "speaker": 0,
                    "minute": 95,
                    "text": "Thanks for the bed checks Marta & Eli 🙏",
                },
            ],
        },
        {
            "days_ago": 18,
            "messages": [
                {
                    "id": 27,
                    "speaker": 1,
                    "minute": 0,
                    "text": "Compost arrived, but it's only 2 cubic metres, not 3.",
                },
                {
                    "id": 28,
                    "speaker": 2,
                    "minute": 3,
                    "image": "A dark compost pile on a blue tarp next to a wooden shed, roughly knee high.",
                    "text": "Here it is",
                },
                {
                    "id": 29,
                    "speaker": 0,
                    "minute": 10,
                    "text": "Ugh. I'll call the depot. The invoice says €140 for 3.",
                    "reply_to": 27,
                },
                {"id": 30, "speaker": 4, "minute": 14, "text": "😬"},
                {
                    "id": 31,
                    "speaker": 0,
                    "minute": 40,
                    "text": "Depot says the third metre comes Wednesday. And they knocked the price down to €120 total for the trouble.",
                },
                {"id": 32, "speaker": 3, "minute": 42, "text": "Nice"},
                {
                    "id": 33,
                    "speaker": 0,
                    "minute": 55,
                    "text": "Eli, I left the shed key in your letterbox this morning, the one with the green tag.",
                },
                {
                    "id": 34,
                    "speaker": 1,
                    "minute": 70,
                    "text": "Got it, thanks!",
                    "reply_to": 33,
                },
                {
                    "id": 35,
                    "speaker": 4,
                    "minute": 80,
                    "text": "I bought a new hoe for the tool library, €18. Receipt is in the shed tin.",
                },
                {"id": 36, "speaker": 2, "minute": 82, "text": "Who reimburses that?"},
                {
                    "id": 37,
                    "speaker": 0,
                    "minute": 85,
                    "text": "Kitty covers anything under €25, just write it in the book.",
                    "reply_to": 36,
                },
                {
                    "id": 38,
                    "speaker": 3,
                    "minute": 100,
                    "voice": "Hey, quick one. The tap for the water butt was out of stock, so I ordered one online, it should come next week. Until then please don't fill the butt past halfway, because it drips from the seam.",
                },
                {"id": 39, "speaker": 2, "minute": 104, "text": "Noted 👍"},
                {
                    "id": 40,
                    "speaker": 1,
                    "minute": 130,
                    "text": "Working party: how about Saturday the 14th?",
                },
                {"id": 41, "speaker": 4, "minute": 133, "text": "Works for me"},
                {"id": 42, "speaker": 2, "minute": 135, "text": "I'm in"},
                {
                    "id": 43,
                    "speaker": 0,
                    "minute": 140,
                    "text": "Saturday 14th it is, provisionally. I'll confirm once I know the skip is booked.",
                },
                {
                    "id": 44,
                    "speaker": 3,
                    "minute": 150,
                    "text": "Can't do the 14th morning, maybe afternoon",
                },
                {
                    "id": 45,
                    "speaker": 1,
                    "minute": 160,
                    "text": "Fees: 6 of 12 plots paid so far.",
                },
                {"id": 46, "speaker": 0, "minute": 162, "text": "Thanks for tracking!"},
                {
                    "id": 47,
                    "speaker": 4,
                    "minute": 170,
                    "text": "Is the plot 4 person still around? Haven't seen them since spring.",
                },
                {
                    "id": 48,
                    "speaker": 2,
                    "minute": 175,
                    "text": "No idea honestly.",
                    "reply_to": 47,
                },
            ],
        },
        {
            "days_ago": 13,
            "messages": [
                {
                    "id": 49,
                    "speaker": 0,
                    "minute": 0,
                    "text": "Change of plan: the working party is Sunday the 15th, not Saturday. 10:00 start. The skip can only come on Sunday.",
                },
                {"id": 50, "speaker": 1, "minute": 5, "text": "Fine by me"},
                {
                    "id": 51,
                    "speaker": 3,
                    "minute": 7,
                    "text": "Even better, I can do Sunday morning",
                },
                {
                    "id": 52,
                    "speaker": 2,
                    "minute": 20,
                    "voice": "Morning everyone, so for Sunday I'll bring the big flask of tea and some biscuits, and I've got two spare pairs of gloves if anyone forgets theirs. I can't stay past twelve though.",
                },
                {
                    "id": 53,
                    "speaker": 3,
                    "minute": 31,
                    "voice": "I've got twenty-four tomato seedlings going spare, mostly cherry ones. I'll bring them on Sunday, first come first served, I'd say two per plot.",
                },
                {
                    "id": 54,
                    "speaker": 4,
                    "minute": 35,
                    "text": "Yes please, two for me!",
                },
                {
                    "id": 55,
                    "speaker": 3,
                    "minute": 60,
                    "image": "Close-up of a new brass tap fitted to a green water butt, with no drips visible.",
                    "text": "Tap is in, butt fixed",
                },
                {
                    "id": 56,
                    "speaker": 0,
                    "minute": 62,
                    "text": "Brilliant, so we can fill it up again?",
                    "reply_to": 55,
                },
                {
                    "id": 57,
                    "speaker": 3,
                    "minute": 64,
                    "text": "Yes, all the way.",
                    "reply_to": 56,
                },
                {"id": 58, "speaker": 1, "minute": 90, "text": "Fees: 9 of 12 paid."},
                {
                    "id": 59,
                    "speaker": 1,
                    "minute": 95,
                    "text": "Reminder: fees are due by the 15th, that's this Sunday.",
                },
                {
                    "id": 60,
                    "speaker": 2,
                    "minute": 97,
                    "text": "Paid mine this morning",
                },
                {
                    "id": 61,
                    "speaker": 1,
                    "minute": 99,
                    "text": "Yep, you're one of the 9",
                    "reply_to": 60,
                },
                {
                    "id": 62,
                    "speaker": 4,
                    "minute": 110,
                    "text": "Should we do a harvest share box at the gate? People leave extras, others take them.",
                },
                {
                    "id": 63,
                    "speaker": 0,
                    "minute": 115,
                    "text": "Nice idea, let's talk about it on Sunday.",
                    "reply_to": 62,
                },
                {"id": 64, "speaker": 2, "minute": 118, "text": "Love it"},
                {
                    "id": 65,
                    "speaker": 3,
                    "minute": 140,
                    "text": "Third metre of compost came on Wednesday by the way, it's behind the shed.",
                },
                {
                    "id": 66,
                    "speaker": 0,
                    "minute": 145,
                    "text": "Great, so we have the full 3 now.",
                },
                {
                    "id": 67,
                    "speaker": 4,
                    "minute": 160,
                    "text": "Who's bringing the wheelbarrows?",
                },
                {"id": 68, "speaker": 1, "minute": 162, "text": "I have one"},
                {
                    "id": 69,
                    "speaker": 0,
                    "minute": 165,
                    "text": "The shed has two, Eli has the key.",
                },
                {"id": 70, "speaker": 2, "minute": 180, "text": "See you Sunday 🌞"},
                {"id": 71, "speaker": 4, "minute": 182, "text": "👋"},
                {"id": 72, "speaker": 3, "minute": 185, "text": "Sunday 10:00!"},
            ],
        },
        {
            "days_ago": 8,
            "messages": [
                {
                    "id": 73,
                    "speaker": 0,
                    "minute": 0,
                    "text": "Working party recap: 7 people came, we spread all 3 cubic metres of compost, cleared the path by plots 9–12, and Tobias's seedlings all went.",
                },
                {
                    "id": 74,
                    "speaker": 4,
                    "minute": 5,
                    "text": "Thanks everyone, that was fun",
                },
                {
                    "id": 75,
                    "speaker": 2,
                    "minute": 30,
                    "image": "A bean leaf covered in small black insects, with a ruler showing the leaf is about 8 cm long.",
                    "text": "Bed 7 has aphids, quite a lot",
                },
                {
                    "id": 76,
                    "speaker": 1,
                    "minute": 33,
                    "text": "Oh no. Spray?",
                    "reply_to": 75,
                },
                {
                    "id": 77,
                    "speaker": 0,
                    "minute": 38,
                    "text": "No chemicals please, it's in our rules. Soapy water first.",
                },
                {
                    "id": 78,
                    "speaker": 3,
                    "minute": 40,
                    "text": "Soapy water works, I did it last year.",
                },
                {"id": 79, "speaker": 4, "minute": 45, "text": "Agree, soapy water."},
                {
                    "id": 80,
                    "speaker": 0,
                    "minute": 50,
                    "text": "OK, decided: soapy water on bed 7 every two days for a week. Marta's doing it.",
                },
                {
                    "id": 81,
                    "speaker": 2,
                    "minute": 52,
                    "text": "Yes, I'll do it.",
                    "reply_to": 80,
                },
                {
                    "id": 82,
                    "speaker": 1,
                    "minute": 70,
                    "text": "Water supply to the allotment standpipes will be shut off on Thursday from 08:00 to 14:00 for maintenance.",
                    "forwarded": "Authored parks office notice",
                },
                {
                    "id": 83,
                    "speaker": 4,
                    "minute": 73,
                    "text": "So water Wednesday evening then",
                    "reply_to": 82,
                },
                {"id": 84, "speaker": 2, "minute": 75, "text": "Or Thursday after two"},
                {
                    "id": 85,
                    "speaker": 1,
                    "minute": 100,
                    "text": "Fees: 11 of 12 paid. Only plot 4 left.",
                },
                {
                    "id": 86,
                    "speaker": 0,
                    "minute": 104,
                    "text": "I'll write to plot 4.",
                },
                {
                    "id": 87,
                    "speaker": 3,
                    "minute": 120,
                    "text": "Harvest share box: I could build one from the old pallets, maybe 60 cm wide?",
                },
                {"id": 88, "speaker": 4, "minute": 122, "text": "Yes!! 🙌"},
                {
                    "id": 89,
                    "speaker": 0,
                    "minute": 126,
                    "text": "Let's see if the committee is OK with a box at the gate first. Not agreed yet.",
                    "reply_to": 87,
                },
                {
                    "id": 90,
                    "speaker": 2,
                    "minute": 140,
                    "text": "Still nobody for Sunday watering btw",
                },
                {
                    "id": 91,
                    "speaker": 1,
                    "minute": 143,
                    "text": "I could maybe do every other Sunday? Not promising",
                },
                {
                    "id": 92,
                    "speaker": 0,
                    "minute": 150,
                    "voice": "Hi all, about the Sunday watering: let's not fix anyone yet. If it's hot, whoever is around waters the seedlings, and we'll sort out a proper rota at the next meeting.",
                },
                {"id": 93, "speaker": 4, "minute": 151, "text": "👍"},
                {"id": 94, "speaker": 3, "minute": 155, "text": "Fine"},
                {
                    "id": 95,
                    "speaker": 1,
                    "minute": 200,
                    "text": "Also: I'm away from the 1st to the 10th, I'll pass the shed key to Noor before I go.",
                },
                {
                    "id": 96,
                    "speaker": 4,
                    "minute": 202,
                    "text": "Sure, I'll take it.",
                    "reply_to": 95,
                },
            ],
        },
        {
            "days_ago": 3,
            "messages": [
                {
                    "id": 97,
                    "speaker": 1,
                    "minute": 0,
                    "text": "Key handed to Noor yesterday evening 🔑",
                },
                {
                    "id": 98,
                    "speaker": 4,
                    "minute": 2,
                    "text": "Yes, I have it. It's on the hook in my hallway.",
                    "reply_to": 97,
                },
                {
                    "id": 99,
                    "speaker": 2,
                    "minute": 20,
                    "text": "Bed 7 update: aphids mostly gone after three rounds of soapy water.",
                },
                {
                    "id": 100,
                    "speaker": 0,
                    "minute": 24,
                    "text": "Great news",
                    "reply_to": 99,
                },
                {
                    "id": 101,
                    "speaker": 0,
                    "minute": 40,
                    "text": "Plot 4 replied: they're giving up the plot. So it's free from next month.",
                },
                {"id": 102, "speaker": 3, "minute": 42, "text": "Waiting list?"},
                {
                    "id": 103,
                    "speaker": 0,
                    "minute": 45,
                    "text": "Two people on the list. The first one gets the offer; if they say no, it goes to the second.",
                    "reply_to": 102,
                },
                {
                    "id": 104,
                    "speaker": 4,
                    "minute": 60,
                    "text": "Committee said yes to the harvest share box 🎉",
                },
                {
                    "id": 105,
                    "speaker": 3,
                    "minute": 63,
                    "text": "I'll build it this weekend then",
                    "reply_to": 104,
                },
                {
                    "id": 106,
                    "speaker": 2,
                    "minute": 80,
                    "text": "Next meeting? We still need the Sunday rota.",
                },
                {
                    "id": 107,
                    "speaker": 0,
                    "minute": 85,
                    "text": "Proposing Tuesday the 3rd, 19:00, at the shed. Any objections?",
                },
                {
                    "id": 108,
                    "speaker": 1,
                    "minute": 88,
                    "text": "I'm away then, go ahead without me",
                },
                {"id": 109, "speaker": 4, "minute": 90, "text": "Works"},
                {
                    "id": 110,
                    "speaker": 3,
                    "minute": 120,
                    "voice": "Small thing: the second wheelbarrow has a flat tyre. I can get a new inner tube, around eight euros, unless anyone has a pump that would do it.",
                },
                {
                    "id": 111,
                    "speaker": 2,
                    "minute": 122,
                    "text": "I have a pump, I'll bring it Thursday",
                },
                {"id": 112, "speaker": 0, "minute": 130, "text": "Thanks all ❤️"},
            ],
        },
    ],
}

# Ten look-alike score labels; the missing pages of copy 7 are reported later.
_SCORE_COPIES = [
    {
        "id": 12 + n,
        "speaker": 1,
        "minute": 64 + 3 * n,
        "text": f"Copy {n} of the new score is in the green box under the piano. "
        "Sign it out on the clipboard and bring it back after the concert.",
    }
    for n in range(1, 11)
]

CHOIR = {
    "name": "choir",
    "title": "Authored community choir",
    "language": "en",
    "speakers": [
        "Authored Rosa",
        "Authored Felix",
        "Authored Hanna",
        "Authored Owen",
        "Authored Lise",
        "Authored Kai",
    ],
    "days": [
        {
            "days_ago": 22,
            "messages": [
                {
                    "id": 1,
                    "speaker": 0,
                    "minute": 0,
                    "text": "Hello singers! Spring concert is booked: Saturday the 12th at 19:00 in the old library hall.",
                },
                {"id": 2, "speaker": 5, "minute": 3, "text": "Yay 🎶"},
                {"id": 3, "speaker": 2, "minute": 5, "text": "Finally!"},
                {
                    "id": 4,
                    "speaker": 1,
                    "minute": 12,
                    "text": "Tickets: I suggest €12 adults, €8 concessions, kids under 12 free.",
                },
                {
                    "id": 5,
                    "speaker": 4,
                    "minute": 15,
                    "text": "Sounds fair.",
                    "reply_to": 4,
                },
                {
                    "id": 6,
                    "speaker": 0,
                    "minute": 18,
                    "text": "Agreed, let's go with Felix's prices.",
                },
                {"id": 7, "speaker": 3, "minute": 25, "text": "What are we wearing?"},
                {
                    "id": 8,
                    "speaker": 0,
                    "minute": 27,
                    "text": "All black, plus a red scarf. Hanna, could you check how many scarves we have?",
                    "reply_to": 7,
                },
                {
                    "id": 9,
                    "speaker": 2,
                    "minute": 40,
                    "voice": "Hi, so I looked in the costume box, we have eleven red scarves and some of them are pretty faded. I can sew new ones, I have enough fabric for about thirty, I just need a week or two.",
                },
                {
                    "id": 10,
                    "speaker": 0,
                    "minute": 45,
                    "text": "You're a star. Thirty is plenty, we're 26.",
                    "reply_to": 9,
                },
                {
                    "id": 11,
                    "speaker": 0,
                    "minute": 60,
                    "text": "Reminder: rehearsal tonight at 19:00 in room 4.",
                },
                {"id": 12, "speaker": 5, "minute": 62, "text": "See you there"},
                *_SCORE_COPIES,
                {
                    "id": 23,
                    "speaker": 1,
                    "minute": 97,
                    "text": "That's all ten copies labelled.",
                },
                {
                    "id": 24,
                    "speaker": 3,
                    "minute": 100,
                    "text": "Who's singing the lullaby solo?",
                },
                {
                    "id": 25,
                    "speaker": 0,
                    "minute": 103,
                    "text": "I was hoping you would, Owen!",
                    "reply_to": 24,
                },
                {
                    "id": 26,
                    "speaker": 3,
                    "minute": 106,
                    "text": "Go on then, I'll do it.",
                    "reply_to": 25,
                },
            ],
        },
        {
            "days_ago": 17,
            "messages": [
                {
                    "id": 27,
                    "speaker": 0,
                    "minute": 0,
                    "text": "Reminder: rehearsal tonight at 19:00 in room 4.",
                },
                {
                    "id": 28,
                    "speaker": 4,
                    "minute": 30,
                    "text": "Copy 7 is missing pages 3 and 4, just so you know.",
                },
                {
                    "id": 29,
                    "speaker": 1,
                    "minute": 33,
                    "text": "I'll print them again tomorrow.",
                    "reply_to": 28,
                },
                {
                    "id": 30,
                    "speaker": 2,
                    "minute": 50,
                    "image": "Folded red scarves stacked on a sewing table next to a sewing machine, about a dozen visible.",
                    "text": "First batch!",
                },
                {"id": 31, "speaker": 5, "minute": 52, "text": "Gorgeous ❤️"},
                {
                    "id": 32,
                    "speaker": 0,
                    "minute": 70,
                    "text": "Altos, in the second piece please breathe at bar 32.",
                },
                {
                    "id": 33,
                    "speaker": 0,
                    "minute": 72,
                    "text": "Sorry, not bar 32, bar 23.",
                },
                {
                    "id": 34,
                    "speaker": 3,
                    "minute": 80,
                    "text": "Thanks, I was confused",
                },
                {
                    "id": 35,
                    "speaker": 1,
                    "minute": 100,
                    "text": "Piano tuner can come Thursday morning, €90. I'll pay from the kitty.",
                },
                {
                    "id": 36,
                    "speaker": 4,
                    "minute": 102,
                    "text": "Do we have €90 in the kitty?",
                },
                {
                    "id": 37,
                    "speaker": 1,
                    "minute": 105,
                    "text": "Yes, we have €310 after the bake sale.",
                    "reply_to": 36,
                },
                {
                    "id": 38,
                    "speaker": 5,
                    "minute": 120,
                    "text": "Should we film the concert? My cousin has a decent camera.",
                },
                {
                    "id": 39,
                    "speaker": 2,
                    "minute": 123,
                    "text": "Some people won't want to be filmed.",
                },
                {"id": 40, "speaker": 3, "minute": 125, "text": "Audio only maybe?"},
                {
                    "id": 41,
                    "speaker": 0,
                    "minute": 130,
                    "text": "Let's think about it, no decision yet.",
                },
                {
                    "id": 42,
                    "speaker": 5,
                    "minute": 150,
                    "text": "I can run coffee and cake at the interval if someone lends me an urn.",
                },
                {
                    "id": 43,
                    "speaker": 4,
                    "minute": 155,
                    "text": "The hall might have one, worth asking.",
                    "reply_to": 42,
                },
                {
                    "id": 44,
                    "speaker": 0,
                    "minute": 170,
                    "image": "A draft concert poster in red and black reading 'Spring Concert, Saturday 12th, 19:00, Old Library Hall, tickets €12 / €8'.",
                    "text": "Poster draft, comments welcome",
                },
                {
                    "id": 45,
                    "speaker": 1,
                    "minute": 175,
                    "text": "Looks great",
                    "reply_to": 44,
                },
                {"id": 46, "speaker": 2, "minute": 178, "text": "Maybe a bigger date?"},
                {"id": 47, "speaker": 0, "minute": 180, "text": "Will do 👍"},
                {"id": 48, "speaker": 3, "minute": 185, "text": "Good night all"},
            ],
        },
        {
            "days_ago": 11,
            "messages": [
                {
                    "id": 49,
                    "speaker": 1,
                    "minute": 0,
                    "text": "We regret that the old library hall was double-booked for Saturday the 12th. We can offer Friday the 11th from 18:00 instead.",
                    "forwarded": "Authored hall booking office",
                },
                {
                    "id": 50,
                    "speaker": 0,
                    "minute": 5,
                    "text": "Argh. Friday the 11th then? Doors 19:00, start 19:30?",
                    "reply_to": 49,
                },
                {"id": 51, "speaker": 2, "minute": 8, "text": "Friday is fine for me"},
                {"id": 52, "speaker": 3, "minute": 10, "text": "OK"},
                {
                    "id": 53,
                    "speaker": 4,
                    "minute": 11,
                    "text": "I can only get there by 19:15 on Fridays, but that works for a 19:30 start",
                },
                {"id": 54, "speaker": 5, "minute": 14, "text": "Fine 👍"},
                {
                    "id": 55,
                    "speaker": 0,
                    "minute": 30,
                    "text": "Confirmed: concert Friday the 11th, doors 19:00, start 19:30. I'll redo the poster.",
                },
                {
                    "id": 56,
                    "speaker": 0,
                    "minute": 45,
                    "text": "Reminder: rehearsal tonight at 19:00 in room 6 (room 4 is being painted).",
                },
                {
                    "id": 57,
                    "speaker": 1,
                    "minute": 50,
                    "text": "Room 6 is the one upstairs?",
                    "reply_to": 56,
                },
                {
                    "id": 58,
                    "speaker": 0,
                    "minute": 52,
                    "text": "Yes, first floor, past the lift.",
                    "reply_to": 57,
                },
                {
                    "id": 59,
                    "speaker": 1,
                    "minute": 80,
                    "text": "Piano tuned this morning, receipt in the folder. Kitty now €220.",
                },
                {
                    "id": 60,
                    "speaker": 4,
                    "minute": 90,
                    "voice": "Hey, I asked the caretaker about the urn: they have one, it's free to use, but someone has to sign for it on the day and return it clean by ten pm.",
                },
                {
                    "id": 61,
                    "speaker": 5,
                    "minute": 95,
                    "text": "I'll sign for it.",
                    "reply_to": 60,
                },
                {
                    "id": 62,
                    "speaker": 2,
                    "minute": 110,
                    "text": "Scarves: 20 done, 10 to go.",
                },
                {
                    "id": 63,
                    "speaker": 3,
                    "minute": 130,
                    "text": "Can we do an extra run of the lullaby?",
                },
                {
                    "id": 64,
                    "speaker": 0,
                    "minute": 133,
                    "text": "Extra rehearsal Wednesday 18:30, sopranos only, same room.",
                },
                {
                    "id": 65,
                    "speaker": 4,
                    "minute": 135,
                    "text": "Sopranos only? Owen's a tenor 😅",
                },
                {
                    "id": 66,
                    "speaker": 0,
                    "minute": 137,
                    "text": "Ha, sopranos plus Owen then.",
                    "reply_to": 65,
                },
                {
                    "id": 67,
                    "speaker": 5,
                    "minute": 160,
                    "text": "Filming: I asked around, 4 people said no camera please.",
                },
                {
                    "id": 68,
                    "speaker": 0,
                    "minute": 165,
                    "text": "OK then no video. Audio recording maybe, I'll ask everyone at rehearsal.",
                },
                {
                    "id": 69,
                    "speaker": 1,
                    "minute": 170,
                    "text": "Ticket sales so far: 34.",
                },
                {"id": 70, "speaker": 2, "minute": 175, "text": "Is that good?"},
                {
                    "id": 71,
                    "speaker": 1,
                    "minute": 177,
                    "text": "Last year we sold 80 in total, so we're on track.",
                    "reply_to": 70,
                },
                {"id": 72, "speaker": 3, "minute": 190, "text": "night 🌙"},
                {"id": 73, "speaker": 5, "minute": 192, "text": "🎵"},
                {"id": 74, "speaker": 0, "minute": 195, "text": "New poster tomorrow!"},
            ],
        },
        {
            "days_ago": 6,
            "messages": [
                {
                    "id": 75,
                    "speaker": 3,
                    "minute": 0,
                    "voice": "Hi all, bad news, I've got a throat infection and the doctor says no singing for two weeks. I'm so sorry, I can't do the lullaby solo.",
                },
                {"id": 76, "speaker": 2, "minute": 3, "text": "Oh no, get well soon!"},
                {"id": 77, "speaker": 5, "minute": 5, "text": "Get well Owen 🍵"},
                {
                    "id": 78,
                    "speaker": 0,
                    "minute": 20,
                    "text": "Lise, would you take the lullaby? It sits well for you.",
                },
                {
                    "id": 79,
                    "speaker": 4,
                    "minute": 25,
                    "text": "Yes, happy to.",
                    "reply_to": 78,
                },
                {
                    "id": 80,
                    "speaker": 0,
                    "minute": 30,
                    "text": "Thank you! Lise sings the lullaby solo then.",
                },
                {
                    "id": 81,
                    "speaker": 0,
                    "minute": 40,
                    "image": "A new concert poster in red and black reading 'Spring Concert, Friday 11th, 19:30, Old Library Hall, tickets €12 / €8, under 12s free'.",
                    "text": "New poster",
                },
                {
                    "id": 82,
                    "speaker": 1,
                    "minute": 42,
                    "text": "Printing 40 copies at the corner shop, €0.30 each.",
                    "reply_to": 81,
                },
                {"id": 83, "speaker": 2, "minute": 60, "text": "All 30 scarves done!"},
                {
                    "id": 84,
                    "speaker": 5,
                    "minute": 62,
                    "text": "Legend",
                    "reply_to": 83,
                },
                {
                    "id": 85,
                    "speaker": 0,
                    "minute": 80,
                    "text": "Reminder: rehearsal tonight at 19:00 in room 6.",
                },
                {"id": 86, "speaker": 1, "minute": 110, "text": "Ticket sales: 61."},
                {"id": 87, "speaker": 4, "minute": 112, "text": "🎉"},
                {
                    "id": 88,
                    "speaker": 0,
                    "minute": 130,
                    "text": "Audio recording: at rehearsal 22 said yes, 2 said no. So we record audio, but only share it within the choir.",
                },
                {"id": 89, "speaker": 2, "minute": 133, "text": "Good compromise"},
                {
                    "id": 90,
                    "speaker": 5,
                    "minute": 140,
                    "text": "Interval: I'll do coffee and lemon cake. Can someone do tea?",
                },
                {
                    "id": 91,
                    "speaker": 2,
                    "minute": 145,
                    "text": "I can bring cups but not tea, sorry.",
                },
                {
                    "id": 92,
                    "speaker": 0,
                    "minute": 150,
                    "voice": "Just a reminder about the warm-up on the day: we meet at six in room 6, ten minutes of breathing, then the first piece once through, and then we walk over to the hall together.",
                },
                {
                    "id": 93,
                    "speaker": 3,
                    "minute": 155,
                    "text": "I'll come and listen at least",
                },
                {
                    "id": 94,
                    "speaker": 4,
                    "minute": 157,
                    "text": "Front row for you!",
                    "reply_to": 93,
                },
                {
                    "id": 95,
                    "speaker": 1,
                    "minute": 170,
                    "text": "Kitty after posters: €208.",
                },
                {"id": 96, "speaker": 0, "minute": 175, "text": "Thanks Felix"},
            ],
        },
        {
            "days_ago": 2,
            "messages": [
                {
                    "id": 97,
                    "speaker": 0,
                    "minute": 0,
                    "text": "Two days to go! Final reminder: black clothes, red scarf, folder, water bottle.",
                },
                {
                    "id": 98,
                    "speaker": 1,
                    "minute": 10,
                    "text": "Ticket sales: 88. Better than last year!",
                },
                {"id": 99, "speaker": 5, "minute": 12, "text": "Wooo"},
                {
                    "id": 100,
                    "speaker": 2,
                    "minute": 30,
                    "image": "A whiteboard sketch of the stage with three rows: sopranos left, altos right, tenors and basses at the back, and a star marked 'Lise' at the front centre.",
                    "text": "Seating plan from rehearsal",
                },
                {
                    "id": 101,
                    "speaker": 4,
                    "minute": 33,
                    "text": "Do I stand at the front for the whole concert or just the solo?",
                    "reply_to": 100,
                },
                {
                    "id": 102,
                    "speaker": 0,
                    "minute": 40,
                    "text": "Just the solo, then back to your row.",
                    "reply_to": 101,
                },
                {
                    "id": 103,
                    "speaker": 5,
                    "minute": 60,
                    "text": "Urn picked up and signed for ✅",
                },
                {
                    "id": 104,
                    "speaker": 1,
                    "minute": 80,
                    "text": "Does anyone know if the hall has a ramp at the side door? A guest asked.",
                },
                {
                    "id": 105,
                    "speaker": 3,
                    "minute": 85,
                    "text": "Not sure, I've only used the front door",
                },
                {
                    "id": 106,
                    "speaker": 0,
                    "minute": 100,
                    "text": "Still no tea volunteer btw, otherwise it's coffee only",
                },
                {
                    "id": 107,
                    "speaker": 3,
                    "minute": 110,
                    "text": "Throat better already, but no singing yet 🤐",
                },
                {"id": 108, "speaker": 2, "minute": 120, "text": "See you Friday!"},
                {"id": 109, "speaker": 4, "minute": 121, "text": "Friday!! 🎶"},
                {"id": 110, "speaker": 0, "minute": 125, "text": "❤️"},
            ],
        },
    ],
}

_WEEKDAYS = ["понедельник", "вторник", "среду", "четверг", "пятницу"]


def _homework(first_id, first_minute, page):
    """A week of near-identical forwarded homework posts that differ in numbers."""
    return [
        {
            "id": first_id + i,
            "speaker": 0,
            "minute": first_minute + i,
            "text": f"ДЗ на {day}: математика стр. {page + 2 * i}, № {3 + i} и "
            f"{5 + i}; русский язык упр. {100 + page + 4 * i}; чтение стр. "
            f"{20 + page + 4 * i}–{23 + page + 4 * i}.",
            "forwarded": "Authored классный руководитель",
        }
        for i, day in enumerate(_WEEKDAYS)
    ]


_HOMEWORK_1 = _homework(15, 80, 30)
_HOMEWORK_2 = _homework(56, 60, 40)

SCHOOL = {
    "name": "school",
    "title": "Authored родители 3Б",
    "language": "ru",
    "speakers": [
        "Authored Вера",
        "Authored Игорь",
        "Authored Света",
        "Authored Тимур",
        "Authored Ксения",
    ],
    "days": [
        {
            "days_ago": 21,
            "messages": [
                {
                    "id": 1,
                    "speaker": 0,
                    "minute": 0,
                    "text": "Доброе утро! В конце месяца у нашей учительницы день рождения. Предлагаю скинуться по 500 ₽ на подарок, собираю я до пятницы.",
                },
                {"id": 2, "speaker": 1, "minute": 3, "text": "Доброе 👍"},
                {"id": 3, "speaker": 2, "minute": 5, "text": "А что дарим?"},
                {
                    "id": 4,
                    "speaker": 0,
                    "minute": 8,
                    "text": "Думаю, сертификат в книжный, плюс цветы от класса",
                    "reply_to": 3,
                },
                {"id": 5, "speaker": 3, "minute": 10, "text": "+"},
                {"id": 6, "speaker": 4, "minute": 12, "text": "Я за сертификат"},
                {
                    "id": 7,
                    "speaker": 1,
                    "minute": 30,
                    "voice": "Всем привет, я по поводу экскурсии. Узнавал в музее хлеба: групповой билет на детей выходит по четыреста пятьдесят, автобус ещё по пятьсот с человека, итого девятьсот пятьдесят. Могут нас взять в четверг, четырнадцатого, с десяти утра.",
                },
                {
                    "id": 8,
                    "speaker": 0,
                    "minute": 35,
                    "text": "Спасибо! 950 нормально, по-моему. Кто против?",
                    "reply_to": 7,
                },
                {"id": 9, "speaker": 2, "minute": 37, "text": "Нормально"},
                {"id": 10, "speaker": 4, "minute": 40, "text": "Ок"},
                {
                    "id": 11,
                    "speaker": 3,
                    "minute": 60,
                    "text": "Уважаемые родители! Экскурсия в музей хлеба 14-го согласована. Выезд от школы в 9:00, возвращение к 14:00. Нужны два сопровождающих родителя.",
                    "forwarded": "Authored классный руководитель",
                },
                {
                    "id": 12,
                    "speaker": 3,
                    "minute": 62,
                    "text": "Я могу поехать",
                    "reply_to": 11,
                },
                {
                    "id": 13,
                    "speaker": 2,
                    "minute": 64,
                    "text": "Я тоже могу, если не заболею 😅",
                    "reply_to": 11,
                },
                {
                    "id": 14,
                    "speaker": 0,
                    "minute": 70,
                    "text": "Отлично, Тимур и Света сопровождают. Деньги за экскурсию сдаём до 10-го.",
                },
                *_HOMEWORK_1,
                {"id": 20, "speaker": 1, "minute": 110, "text": "Спасибо!"},
                {
                    "id": 21,
                    "speaker": 4,
                    "minute": 115,
                    "text": "А сменку в пятницу надо?",
                },
                {
                    "id": 22,
                    "speaker": 0,
                    "minute": 117,
                    "text": "Надо, физра будет в зале",
                    "reply_to": 21,
                },
                {
                    "id": 23,
                    "speaker": 2,
                    "minute": 130,
                    "text": "Народ, а собрание когда?",
                },
                {
                    "id": 24,
                    "speaker": 0,
                    "minute": 133,
                    "text": "Родительское собрание в среду в 18:30, каб. 214.",
                    "reply_to": 23,
                },
                {
                    "id": 25,
                    "speaker": 0,
                    "minute": 135,
                    "text": "Ой, не 214, а 241, второй этаж.",
                },
                {"id": 26, "speaker": 3, "minute": 140, "text": "Понял, 241"},
            ],
        },
        {
            "days_ago": 16,
            "messages": [
                {
                    "id": 27,
                    "speaker": 0,
                    "minute": 0,
                    "text": "Сдали на подарок 12 человек из 26. Напоминаю, до пятницы 🙏",
                },
                {
                    "id": 28,
                    "speaker": 4,
                    "minute": 20,
                    "image": "Синий мешок для обуви с вышитым жирафом висит на крючке в школьной раздевалке.",
                    "text": "Чей мешок? Висит в раздевалке уже неделю",
                },
                {
                    "id": 29,
                    "speaker": 1,
                    "minute": 25,
                    "text": "Не наш",
                    "reply_to": 28,
                },
                {
                    "id": 30,
                    "speaker": 2,
                    "minute": 27,
                    "text": "Тоже не наш",
                    "reply_to": 28,
                },
                {
                    "id": 31,
                    "speaker": 3,
                    "minute": 40,
                    "text": "По собранию: я не успеваю к 18:30, можно чуть позже?",
                },
                {
                    "id": 32,
                    "speaker": 0,
                    "minute": 45,
                    "text": "Спрошу у учительницы",
                    "reply_to": 31,
                },
                {
                    "id": 33,
                    "speaker": 2,
                    "minute": 60,
                    "voice": "Привет всем. Вера, слушай, мы тут с мужем посчитали: если сертификат на десять тысяч, то с двадцати шести человек по четыреста хватает с запасом, даже на цветы. Может, по четыреста сделаем, а не по пятьсот?",
                },
                {
                    "id": 34,
                    "speaker": 0,
                    "minute": 65,
                    "text": "Логично. Кто за 400?",
                    "reply_to": 33,
                },
                {"id": 35, "speaker": 4, "minute": 66, "text": "За"},
                {"id": 36, "speaker": 1, "minute": 67, "text": "+"},
                {"id": 37, "speaker": 3, "minute": 70, "text": "За 400"},
                {
                    "id": 38,
                    "speaker": 0,
                    "minute": 75,
                    "text": "Решили: по 400 ₽. Кто уже сдал 500 — верну по 100 или оставим на цветы, как скажете.",
                },
                {
                    "id": 39,
                    "speaker": 1,
                    "minute": 78,
                    "text": "Оставляй на цветы",
                    "reply_to": 38,
                },
                {
                    "id": 40,
                    "speaker": 0,
                    "minute": 100,
                    "text": "Родительское собрание переносится на четверг, 19:00, тот же кабинет.",
                    "forwarded": "Authored классный руководитель",
                },
                {
                    "id": 41,
                    "speaker": 3,
                    "minute": 102,
                    "text": "О, отлично, успеваю",
                    "reply_to": 40,
                },
                {
                    "id": 42,
                    "speaker": 4,
                    "minute": 120,
                    "text": "Кто-нибудь знает, жалюзи в классе будут менять? Нас в прошлом году просили скинуться",
                },
                {
                    "id": 43,
                    "speaker": 1,
                    "minute": 123,
                    "text": "Вроде школа обещала сама",
                },
                {
                    "id": 44,
                    "speaker": 0,
                    "minute": 125,
                    "text": "Пока ничего не известно, спрошу на собрании",
                },
                {
                    "id": 45,
                    "speaker": 2,
                    "minute": 140,
                    "text": "Спасибо, Вера, ты супер ❤️",
                },
                {"id": 46, "speaker": 0, "minute": 142, "text": "😊"},
                {
                    "id": 47,
                    "speaker": 3,
                    "minute": 150,
                    "text": "Деньги на экскурсию кому переводить?",
                },
                {
                    "id": 48,
                    "speaker": 0,
                    "minute": 152,
                    "text": "Мне, по номеру карты, как обычно. С пометкой «музей».",
                    "reply_to": 47,
                },
            ],
        },
        {
            "days_ago": 10,
            "messages": [
                {
                    "id": 49,
                    "speaker": 0,
                    "minute": 0,
                    "text": "Собрание вчера: 1) жалюзи школа меняет сама, денег не сдаём. 2) Осенний праздник 28-го в актовом зале, нужны 3 родителя на чаепитие. 3) Контрольная по математике в четверг.",
                },
                {"id": 50, "speaker": 4, "minute": 5, "text": "Спасибо за отчёт!"},
                {
                    "id": 51,
                    "speaker": 1,
                    "minute": 8,
                    "text": "Ура, жалюзи не наша забота 😄",
                },
                {
                    "id": 52,
                    "speaker": 2,
                    "minute": 20,
                    "voice": "Всем привет. Я, к сожалению, на экскурсию четырнадцатого не смогу, меня на работе ставят на смену. Простите. Может, кто-то вместо меня?",
                },
                {
                    "id": 53,
                    "speaker": 4,
                    "minute": 30,
                    "text": "Я могу вместо Светы",
                    "reply_to": 52,
                },
                {
                    "id": 54,
                    "speaker": 0,
                    "minute": 33,
                    "text": "Спасибо, Ксения! Значит, сопровождают Тимур и Ксения.",
                },
                {"id": 55, "speaker": 3, "minute": 35, "text": "👍"},
                *_HOMEWORK_2,
                {
                    "id": 61,
                    "speaker": 1,
                    "minute": 70,
                    "text": "А контрольная в этот четверг или в следующий?",
                },
                {
                    "id": 62,
                    "speaker": 0,
                    "minute": 72,
                    "text": "Вроде в этот, четырнадцатого? Ой нет, четырнадцатого же экскурсия. Уточню.",
                    "reply_to": 61,
                },
                {
                    "id": 63,
                    "speaker": 0,
                    "minute": 90,
                    "text": "Уточнила: контрольная по математике в пятницу, 15-го, не в четверг.",
                },
                {"id": 64, "speaker": 2, "minute": 92, "text": "Спасибо"},
                {
                    "id": 65,
                    "speaker": 3,
                    "minute": 110,
                    "text": "Кто на праздник 28-го на чаепитие?",
                },
                {"id": 66, "speaker": 4, "minute": 112, "text": "Я испеку пирог"},
                {
                    "id": 67,
                    "speaker": 1,
                    "minute": 113,
                    "text": "Могу принести чай и одноразовые стаканы",
                },
                {
                    "id": 68,
                    "speaker": 0,
                    "minute": 115,
                    "text": "Нужен ещё один человек на чаепитие",
                },
                {
                    "id": 69,
                    "speaker": 2,
                    "minute": 140,
                    "text": "Сдала на подарок 400 ✅",
                },
                {
                    "id": 70,
                    "speaker": 0,
                    "minute": 143,
                    "text": "Получила, спасибо!",
                    "reply_to": 69,
                },
                {
                    "id": 71,
                    "speaker": 0,
                    "minute": 150,
                    "text": "На подарок сдали 21 из 26.",
                },
                {
                    "id": 72,
                    "speaker": 3,
                    "minute": 170,
                    "text": "Мешок с жирафом так никто и не забрал?",
                },
                {
                    "id": 73,
                    "speaker": 4,
                    "minute": 172,
                    "text": "Висит ещё",
                    "reply_to": 72,
                },
                {"id": 74, "speaker": 1, "minute": 180, "text": "Спокойной ночи всем"},
            ],
        },
        {
            "days_ago": 5,
            "messages": [
                {
                    "id": 75,
                    "speaker": 3,
                    "minute": 0,
                    "image": "Дети в куртках у входа в музей; на табличке у двери написано «Музей хлеба», часы над входом показывают 10:05.",
                    "text": "Доехали, всё ок!",
                },
                {
                    "id": 76,
                    "speaker": 0,
                    "minute": 3,
                    "text": "Ура, спасибо!",
                    "reply_to": 75,
                },
                {
                    "id": 77,
                    "speaker": 4,
                    "minute": 60,
                    "voice": "Нам тут экскурсовод сказал, что в конце дети сами пекут булочки, так что вернёмся чуть позже, не к двум, а где-то к половине третьего. Встречайте у школы.",
                },
                {"id": 78, "speaker": 2, "minute": 62, "text": "Ок, поняла"},
                {"id": 79, "speaker": 1, "minute": 64, "text": "👌"},
                {
                    "id": 80,
                    "speaker": 3,
                    "minute": 150,
                    "text": "Вернулись в 14:35, всех раздали родителям 👍",
                },
                {
                    "id": 81,
                    "speaker": 0,
                    "minute": 155,
                    "text": "Спасибо сопровождающим! ❤️",
                },
                {
                    "id": 82,
                    "speaker": 2,
                    "minute": 160,
                    "text": "Дочка в восторге от булочек 😂",
                },
                {
                    "id": 83,
                    "speaker": 0,
                    "minute": 200,
                    "text": "На подарок сдали 25 из 26. Остался один человек, напишите мне в личку.",
                },
                {
                    "id": 84,
                    "speaker": 1,
                    "minute": 205,
                    "text": "Кто ещё не сдал, отзовитесь",
                },
                {
                    "id": 85,
                    "speaker": 0,
                    "minute": 220,
                    "image": "Подарочный сертификат книжного магазина на 10 000 рублей в бумажном конверте, рядом букет хризантем.",
                    "text": "Купила сертификат и цветы",
                },
                {
                    "id": 86,
                    "speaker": 4,
                    "minute": 222,
                    "text": "Красота!",
                    "reply_to": 85,
                },
                {"id": 87, "speaker": 3, "minute": 240, "text": "Чек сколько вышло?"},
                {
                    "id": 88,
                    "speaker": 0,
                    "minute": 243,
                    "text": "Сертификат 10 000, цветы 1 200, всего 11 200 — как раз сколько собрали.",
                    "reply_to": 87,
                },
                {"id": 89, "speaker": 2, "minute": 245, "text": "Идеально 👍"},
                {
                    "id": 90,
                    "speaker": 4,
                    "minute": 260,
                    "text": "А третьего человека на чаепитие так и не нашли?",
                },
                {
                    "id": 91,
                    "speaker": 0,
                    "minute": 262,
                    "text": "Пока нет 😔",
                    "reply_to": 90,
                },
                {
                    "id": 92,
                    "speaker": 1,
                    "minute": 270,
                    "text": "Может, кто-то из пап?",
                },
                {
                    "id": 93,
                    "speaker": 3,
                    "minute": 272,
                    "text": "Я в тот день на работе, увы",
                },
                {
                    "id": 94,
                    "speaker": 0,
                    "minute": 300,
                    "text": "Мешок с жирафом отнесла на вахту, если что.",
                },
                {"id": 95, "speaker": 2, "minute": 302, "text": "👍"},
                {"id": 96, "speaker": 4, "minute": 310, "text": "Всем хорошего вечера"},
            ],
        },
        {
            "days_ago": 1,
            "messages": [
                {
                    "id": 97,
                    "speaker": 0,
                    "minute": 0,
                    "text": "Напоминаю: в пятницу контрольная по математике. Повторить таблицу умножения на 7 и 8 и задачи на стр. 52.",
                    "forwarded": "Authored классный руководитель",
                },
                {"id": 98, "speaker": 1, "minute": 5, "text": "Спасибо"},
                {
                    "id": 99,
                    "speaker": 2,
                    "minute": 30,
                    "text": "А кто в итоге фотографирует на празднике?",
                },
                {
                    "id": 100,
                    "speaker": 3,
                    "minute": 33,
                    "text": "Можно попросить учителя информатики, у него хороший фотик",
                    "reply_to": 99,
                },
                {
                    "id": 101,
                    "speaker": 0,
                    "minute": 35,
                    "text": "Можно, но я ещё не спрашивала",
                },
                {
                    "id": 102,
                    "speaker": 4,
                    "minute": 60,
                    "voice": "Девочки и мальчики, напоминаю про праздник двадцать восьмого: начало в пять вечера в актовом зале, дети в белом верхе и чёрном низе. Пирог я испеку, Игорь несёт чай и стаканы.",
                },
                {
                    "id": 103,
                    "speaker": 1,
                    "minute": 62,
                    "text": "Да, помню",
                    "reply_to": 102,
                },
                {
                    "id": 104,
                    "speaker": 0,
                    "minute": 90,
                    "text": "Последний не сдавший сдал, на подарок 26 из 26 🎉",
                },
                {"id": 105, "speaker": 3, "minute": 92, "text": "Ура"},
                {
                    "id": 106,
                    "speaker": 2,
                    "minute": 120,
                    "text": "Подскажите, а продлёнка в день праздника работает?",
                },
                {"id": 107, "speaker": 4, "minute": 125, "text": "Не знаю, вроде да"},
                {
                    "id": 108,
                    "speaker": 0,
                    "minute": 130,
                    "text": "Поздравляем учительницу в понедельник утром, приходите к 8:15 к кабинету.",
                },
                {"id": 109, "speaker": 1, "minute": 131, "text": "Буду"},
                {"id": 110, "speaker": 2, "minute": 133, "text": "👍🏻"},
            ],
        },
    ],
}

# Twelve look-alike box labels; box 7 is corrected later in a written message.
_BOXES = [
    {
        "id": 24 + n,
        "speaker": 1,
        "minute": 20 + n,
        "text": f"Коробка №{n}: кухня, посуда, хрупкое. Не ставить вниз, грузить "
        "последней.",
    }
    for n in range(1, 13)
]

DACHA = {
    "name": "dacha",
    "title": "Authored переезд на дачу",
    "language": "ru",
    "speakers": [
        "Authored Лёша",
        "Authored Наташа",
        "Authored Паша",
        "Authored Дима",
        "Authored Женя",
    ],
    "days": [
        {
            "days_ago": 20,
            "messages": [
                {
                    "id": 1,
                    "speaker": 0,
                    "minute": 0,
                    "text": "Ребят, всем привет! Переезжаем на дачу на лето, нужна помощь с вещами. Планирую субботу 19-го. Кто сможет?",
                },
                {"id": 2, "speaker": 3, "minute": 4, "text": "Я в деле"},
                {
                    "id": 3,
                    "speaker": 4,
                    "minute": 6,
                    "text": "Смогу с утра, после обеда у меня дела",
                },
                {
                    "id": 4,
                    "speaker": 2,
                    "minute": 15,
                    "voice": "Лёх, привет. Газель свободна в субботу, могу сам и повести. Только бензин за тобой, туда-обратно где-то литров сорок выйдет, это примерно три с половиной тысячи.",
                },
                {
                    "id": 5,
                    "speaker": 0,
                    "minute": 18,
                    "text": "Паш, ты лучший 🙏 Бензин конечно мой",
                    "reply_to": 4,
                },
                {
                    "id": 6,
                    "speaker": 1,
                    "minute": 25,
                    "text": "Я тогда на даче с обедом, сделаю плов на всех",
                },
                {"id": 7, "speaker": 3, "minute": 27, "text": "Плов 🔥"},
                {
                    "id": 8,
                    "speaker": 0,
                    "minute": 40,
                    "text": "Сбор у нас в 8:00 в субботу, адрес все знают. Ехать ~90 км.",
                },
                {"id": 9, "speaker": 4, "minute": 42, "text": "Ок"},
                {
                    "id": 10,
                    "speaker": 2,
                    "minute": 50,
                    "text": "Что по объёму? Шкаф будет?",
                },
                {
                    "id": 11,
                    "speaker": 0,
                    "minute": 55,
                    "text": "Шкафа нет, есть диван, холодильник, стиралка и коробок 15",
                    "reply_to": 10,
                },
                {
                    "id": 12,
                    "speaker": 2,
                    "minute": 58,
                    "text": "Влезет. Диван только вчетвером нести",
                    "reply_to": 11,
                },
                {
                    "id": 13,
                    "speaker": 3,
                    "minute": 60,
                    "text": "Нас и будет четверо: я, ты, Лёха, Женя",
                },
                {
                    "id": 14,
                    "speaker": 4,
                    "minute": 62,
                    "text": "Я до обеда, напоминаю 😅",
                    "reply_to": 13,
                },
                {
                    "id": 15,
                    "speaker": 1,
                    "minute": 80,
                    "text": "А холодильник старый зачем везти? Там на даче есть свой",
                },
                {
                    "id": 16,
                    "speaker": 0,
                    "minute": 83,
                    "text": "Хм, тоже верно. Подумаем",
                },
                {
                    "id": 17,
                    "speaker": 2,
                    "minute": 100,
                    "text": "Ремни для крепления у кого-нибудь есть? Мои порвались",
                },
                {
                    "id": 18,
                    "speaker": 3,
                    "minute": 102,
                    "text": "У меня один, но короткий",
                },
                {
                    "id": 19,
                    "speaker": 0,
                    "minute": 110,
                    "text": "Спасибо всем, вы лучшие ❤️",
                },
                {"id": 20, "speaker": 1, "minute": 112, "text": "❤️"},
                {
                    "id": 21,
                    "speaker": 4,
                    "minute": 130,
                    "text": "Кстати, ключ от калитки у кого?",
                },
                {
                    "id": 22,
                    "speaker": 1,
                    "minute": 133,
                    "text": "У соседки из дома 12, она откроет, я её предупрежу",
                    "reply_to": 21,
                },
            ],
        },
        {
            "days_ago": 14,
            "messages": [
                {
                    "id": 23,
                    "speaker": 1,
                    "minute": 0,
                    "image": "Коридор квартиры, заставленный картонными коробками, подписанными маркером; сверху лежит рулон скотча.",
                    "text": "Пакуемся 📦",
                },
                {
                    "id": 24,
                    "speaker": 3,
                    "minute": 5,
                    "text": "Ого сколько",
                    "reply_to": 23,
                },
                *_BOXES,
                {
                    "id": 37,
                    "speaker": 1,
                    "minute": 40,
                    "text": "Коробка №7 — не посуда, там документы и зарядки, её везём в машине, не в газели.",
                },
                {
                    "id": 38,
                    "speaker": 0,
                    "minute": 45,
                    "text": "Ок, №7 беру в машину",
                    "reply_to": 37,
                },
                {
                    "id": 39,
                    "speaker": 0,
                    "minute": 60,
                    "text": "Решили: старый холодильник не везём. Продаём, выставил за 5 000.",
                },
                {"id": 40, "speaker": 4, "minute": 62, "text": "Правильно"},
                {
                    "id": 41,
                    "speaker": 2,
                    "minute": 90,
                    "voice": "Народ, такая тема: газель мне нужно вернуть на базу к трём часам дня в субботу, так что давайте выезжать пораньше. Не в восемь, а в полвосьмого хотя бы.",
                },
                {
                    "id": 42,
                    "speaker": 0,
                    "minute": 93,
                    "text": "Ок, тогда сбор в 7:30!",
                    "reply_to": 41,
                },
                {"id": 43, "speaker": 3, "minute": 95, "text": "Жестоко 😴 но ок"},
                {
                    "id": 44,
                    "speaker": 4,
                    "minute": 97,
                    "text": "Тогда я точно до обеда свободна 😄",
                },
                {
                    "id": 45,
                    "speaker": 0,
                    "minute": 120,
                    "text": "Кто знает, где купить ремни нормальные недорого?",
                },
                {
                    "id": 46,
                    "speaker": 3,
                    "minute": 122,
                    "text": "В строительном видел по 700 за пару",
                },
                {"id": 47, "speaker": 2, "minute": 124, "text": "Сойдёт"},
                {
                    "id": 48,
                    "speaker": 0,
                    "minute": 150,
                    "text": "Ещё вопрос: старый ковёр кто-нибудь заберёт? Бесплатно",
                },
                {"id": 49, "speaker": 1, "minute": 152, "text": "Не я 😂"},
            ],
        },
        {
            "days_ago": 9,
            "messages": [
                {
                    "id": 50,
                    "speaker": 0,
                    "minute": 0,
                    "text": "Уважаемые садоводы! С 1-го по 30-е число въезд грузовых машин массой более 3,5 т на территорию разрешён только с 8:00 до 20:00. Шлагбаум у второго въезда не работает, пользуйтесь главным.",
                    "forwarded": "Authored правление СНТ",
                },
                {
                    "id": 51,
                    "speaker": 2,
                    "minute": 5,
                    "text": "Газель 3,5, впритык, но проходим 👍",
                    "reply_to": 50,
                },
                {
                    "id": 52,
                    "speaker": 1,
                    "minute": 8,
                    "text": "Значит, через главный въезд",
                },
                {
                    "id": 53,
                    "speaker": 0,
                    "minute": 30,
                    "text": "Холодильник продал! За 4 000, торговались 😅 Забирают в пятницу вечером.",
                },
                {"id": 54, "speaker": 4, "minute": 32, "text": "Норм", "reply_to": 53},
                {
                    "id": 55,
                    "speaker": 3,
                    "minute": 60,
                    "voice": "Лёха, привет. Слушай, я в субботу могу только к девяти подъехать, раньше никак, мне ребёнка на секцию отвезти надо. Сразу на квартиру подъеду, если вы ещё не уедете, или прямо на дачу.",
                },
                {
                    "id": 56,
                    "speaker": 0,
                    "minute": 65,
                    "text": "Блин. Без тебя диван не вынесем. Давай прямо к 9 на квартиру, мы диван последним вынесем",
                    "reply_to": 55,
                },
                {
                    "id": 57,
                    "speaker": 3,
                    "minute": 67,
                    "text": "Договорились",
                    "reply_to": 56,
                },
                {
                    "id": 58,
                    "speaker": 2,
                    "minute": 90,
                    "text": "Я ремни купил, 2 пары, 1 400",
                },
                {
                    "id": 59,
                    "speaker": 0,
                    "minute": 92,
                    "text": "Скину тебе вместе с бензином",
                    "reply_to": 58,
                },
                {
                    "id": 60,
                    "speaker": 1,
                    "minute": 120,
                    "voice": "Ребят, я на даче буду с утра, к вашему приезду плов будет готов где-то к часу. Если кто-то не ест мясо, скажите, сделаю отдельно с овощами.",
                },
                {"id": 61, "speaker": 4, "minute": 123, "text": "Я ем всё 😄"},
                {"id": 62, "speaker": 3, "minute": 125, "text": "Мясо 👍"},
                {
                    "id": 63,
                    "speaker": 0,
                    "minute": 140,
                    "text": "По погоде: в субботу обещают дождь после обеда",
                },
                {
                    "id": 64,
                    "speaker": 2,
                    "minute": 142,
                    "text": "Тем более надо раньше",
                },
                {
                    "id": 65,
                    "speaker": 4,
                    "minute": 160,
                    "text": "Я, кстати, могу остаться до 14, сдвинула дела",
                },
                {
                    "id": 66,
                    "speaker": 0,
                    "minute": 162,
                    "text": "Супер!",
                    "reply_to": 65,
                },
                {
                    "id": 67,
                    "speaker": 1,
                    "minute": 170,
                    "text": "Ключ у соседки, она будет дома с 10",
                },
                {"id": 68, "speaker": 3, "minute": 180, "text": "До субботы!"},
                {"id": 69, "speaker": 0, "minute": 182, "text": "👋"},
                {
                    "id": 70,
                    "speaker": 2,
                    "minute": 185,
                    "text": "Да, всем спать пораньше в пятницу 😂",
                },
                {"id": 71, "speaker": 4, "minute": 187, "text": "😂"},
            ],
        },
        {
            "days_ago": 6,
            "messages": [
                {
                    "id": 72,
                    "speaker": 0,
                    "minute": 0,
                    "text": "Дима, ты подъехал? Мы вынесли всё, кроме дивана",
                },
                {
                    "id": 73,
                    "speaker": 3,
                    "minute": 3,
                    "text": "5 минут, паркуюсь",
                    "reply_to": 72,
                },
                {
                    "id": 74,
                    "speaker": 2,
                    "minute": 30,
                    "image": "Грузовой фургон с открытыми задними дверями: внутри стиральная машина, диван на боку и коробки, всё перетянуто оранжевыми ремнями.",
                    "text": "Загрузились",
                },
                {
                    "id": 75,
                    "speaker": 1,
                    "minute": 33,
                    "text": "Ура! Езжайте аккуратно",
                    "reply_to": 74,
                },
                {"id": 76, "speaker": 4, "minute": 35, "text": "Выехали"},
                {
                    "id": 77,
                    "speaker": 0,
                    "minute": 90,
                    "voice": "Мы на трассе, стоим в пробке перед мостом, там ремонт дороги. Минут на сорок задерживаемся. Наташ, с пловом не торопись.",
                },
                {"id": 78, "speaker": 1, "minute": 92, "text": "Ок, держу на огне"},
                {
                    "id": 79,
                    "speaker": 2,
                    "minute": 150,
                    "text": "Приехали. Шлагбаум у главного въезда открыли",
                },
                {
                    "id": 80,
                    "speaker": 1,
                    "minute": 152,
                    "text": "Ключ у меня, соседка отдала 👍",
                },
                {
                    "id": 81,
                    "speaker": 0,
                    "minute": 200,
                    "text": "Всё выгрузили! Диван на веранде, стиралку пока в сарай, подключим потом",
                },
                {
                    "id": 82,
                    "speaker": 3,
                    "minute": 210,
                    "image": "Стол на веранде дачи: большой казан с пловом, тарелки, вокруг сидят пятеро человек.",
                    "text": "Плов 🔥🔥🔥",
                },
                {"id": 83, "speaker": 4, "minute": 215, "text": "Спасибо, Наташа!"},
                {
                    "id": 84,
                    "speaker": 2,
                    "minute": 230,
                    "text": "Я поехал газель сдавать, успеваю к 15",
                },
                {
                    "id": 85,
                    "speaker": 0,
                    "minute": 232,
                    "text": "Спасибо, Паш! Бензин и ремни переведу вечером",
                    "reply_to": 84,
                },
                {"id": 86, "speaker": 1, "minute": 300, "text": "Ребята, вы золото ❤️"},
                {
                    "id": 87,
                    "speaker": 0,
                    "minute": 320,
                    "text": "Коробка №7 в машине, документы целы 😅",
                },
                {
                    "id": 88,
                    "speaker": 3,
                    "minute": 330,
                    "text": "Стиралку когда подключать будем?",
                },
                {
                    "id": 89,
                    "speaker": 0,
                    "minute": 332,
                    "text": "Нужен шланг подлиннее, сначала куплю",
                    "reply_to": 88,
                },
            ],
        },
        {
            "days_ago": 4,
            "messages": [
                {
                    "id": 90,
                    "speaker": 0,
                    "minute": 0,
                    "text": "Паша, перевёл тебе 4 900: 3 500 бензин и 1 400 ремни. Проверь",
                },
                {
                    "id": 91,
                    "speaker": 2,
                    "minute": 10,
                    "text": "Пришло, спасибо!",
                    "reply_to": 90,
                },
                {
                    "id": 92,
                    "speaker": 1,
                    "minute": 30,
                    "text": "Ковёр так и лежит в квартире. Никому не нужен?",
                },
                {
                    "id": 93,
                    "speaker": 4,
                    "minute": 33,
                    "text": "Может, на барахолку выставить?",
                    "reply_to": 92,
                },
                {
                    "id": 94,
                    "speaker": 0,
                    "minute": 40,
                    "text": "Выставлю, если до выходных никто не заберёт",
                },
                {
                    "id": 95,
                    "speaker": 3,
                    "minute": 60,
                    "text": "Шланг для стиралки какой нужен?",
                },
                {
                    "id": 96,
                    "speaker": 0,
                    "minute": 62,
                    "text": "3 метра, 3/4 дюйма",
                    "reply_to": 95,
                },
                {
                    "id": 97,
                    "speaker": 3,
                    "minute": 64,
                    "text": "Привезу в среду, у меня лишний валяется",
                    "reply_to": 96,
                },
                {
                    "id": 98,
                    "speaker": 1,
                    "minute": 90,
                    "voice": "Всем ещё раз огромное спасибо за субботу. Приглашаем на шашлыки через две недели, в субботу, на дачу, всех вместе с семьями. Точную дату скинем позже.",
                },
                {"id": 99, "speaker": 4, "minute": 92, "text": "С радостью!"},
                {"id": 100, "speaker": 2, "minute": 93, "text": "👍"},
                {
                    "id": 101,
                    "speaker": 0,
                    "minute": 120,
                    "text": "Кстати, ремни тебе вернуть или оставить?",
                },
                {
                    "id": 102,
                    "speaker": 2,
                    "minute": 122,
                    "text": "Оставь себе, на даче пригодятся",
                    "reply_to": 101,
                },
                {"id": 103, "speaker": 3, "minute": 130, "text": "Всем пока 👋"},
            ],
        },
    ],
}

_FLATMATES = ["Аня", "Марко", "Саша", "Лео", "Катя"]
# Eight look-alike rota posts; a written swap later changes week 3.
_ROTA = [
    {
        "id": 18 + week,
        "speaker": 0,
        "minute": 79 + week,
        "text": f"Уборка, неделя {week}: кухня — {_FLATMATES[(week - 1) % 5]}, "
        f"ванная — {_FLATMATES[week % 5]}, мусор — {_FLATMATES[(week + 1) % 5]}. "
        "Меняться только через чат.",
    }
    for week in range(1, 9)
]

FLAT = {
    "name": "flat",
    "title": "Authored flat 5 / квартира",
    "language": "mixed",
    "speakers": [
        "Authored Anya",
        "Authored Marco",
        "Authored Sasha",
        "Authored Leo",
        "Authored Katya",
    ],
    "days": [
        {
            "days_ago": 19,
            "messages": [
                {
                    "id": 1,
                    "speaker": 0,
                    "minute": 0,
                    "text": "Всем привет! Пришёл счёт за свет за сентябрь — 4 860 ₽. Делим на пятерых, по 972.",
                },
                {
                    "id": 2,
                    "speaker": 0,
                    "minute": 1,
                    "image": "Бумажная квитанция за электроэнергию за сентябрь: сумма к оплате 4 680,00 руб., срок оплаты до 20-го.",
                    "text": "Квитанция",
                },
                {
                    "id": 3,
                    "speaker": 1,
                    "minute": 10,
                    "text": "Sorry, English please? 😅 How much do I owe?",
                },
                {
                    "id": 4,
                    "speaker": 2,
                    "minute": 12,
                    "text": "Electricity for September, 972 each, pay Anya by the 20th.",
                    "reply_to": 3,
                },
                {
                    "id": 5,
                    "speaker": 1,
                    "minute": 14,
                    "text": "Thanks! Will send tonight.",
                },
                {
                    "id": 6,
                    "speaker": 3,
                    "minute": 30,
                    "text": "Btw the internet is painfully slow in the evenings again",
                },
                {
                    "id": 7,
                    "speaker": 1,
                    "minute": 32,
                    "text": "Yeah, it's 100 Mbps for five people. There's a 500 Mbps plan for 1,650 a month, we pay 1,200 now.",
                    "reply_to": 6,
                },
                {
                    "id": 8,
                    "speaker": 4,
                    "minute": 35,
                    "text": "Мне и так нормально, я по вечерам не сижу в интернете",
                },
                {
                    "id": 9,
                    "speaker": 2,
                    "minute": 37,
                    "text": "Katya says it's fine for her as is",
                },
                {
                    "id": 10,
                    "speaker": 0,
                    "minute": 40,
                    "text": "Давайте проголосуем: кто за 500 Мбит за 1 650?",
                },
                {"id": 11, "speaker": 1, "minute": 41, "text": "Me"},
                {"id": 12, "speaker": 3, "minute": 42, "text": "+1"},
                {"id": 13, "speaker": 2, "minute": 43, "text": "За"},
                {
                    "id": 14,
                    "speaker": 4,
                    "minute": 45,
                    "text": "Против, 90 рублей в месяц лишних с каждого",
                },
                {
                    "id": 15,
                    "speaker": 3,
                    "minute": 60,
                    "text": "Also is anyone else's radiator cold? Mine is like ice",
                },
                {
                    "id": 16,
                    "speaker": 0,
                    "minute": 62,
                    "text": "Отопление ещё не дали, в городе включают по графику",
                    "reply_to": 15,
                },
                {"id": 17, "speaker": 3, "minute": 63, "text": "Translate pls 🙏"},
                {
                    "id": 18,
                    "speaker": 2,
                    "minute": 65,
                    "text": "Heating isn't on yet, the city turns it on on a schedule",
                    "reply_to": 16,
                },
                *_ROTA,
                {
                    "id": 27,
                    "speaker": 0,
                    "minute": 90,
                    "text": "Это график уборки на два месяца. Если меняетесь — пишите сюда.",
                },
                {
                    "id": 28,
                    "speaker": 1,
                    "minute": 92,
                    "text": "Is this the cleaning rota? I see my name 😄",
                },
                {
                    "id": 29,
                    "speaker": 2,
                    "minute": 93,
                    "text": "Yes, two months of it. Swaps only via the chat.",
                    "reply_to": 28,
                },
                {"id": 30, "speaker": 4, "minute": 100, "text": "Спасибо, Ань"},
            ],
        },
        {
            "days_ago": 14,
            "messages": [
                {
                    "id": 31,
                    "speaker": 0,
                    "minute": 0,
                    "text": "Ой, поправка: за свет не 4 860, а 4 680, я цифры перепутала. Значит, по 936, а не 972.",
                },
                {
                    "id": 32,
                    "speaker": 1,
                    "minute": 3,
                    "text": "So 936 each? I already sent 972",
                },
                {
                    "id": 33,
                    "speaker": 0,
                    "minute": 5,
                    "text": "Верну тебе 36 😊 / I'll send you back 36",
                    "reply_to": 32,
                },
                {
                    "id": 34,
                    "speaker": 1,
                    "minute": 6,
                    "text": "No worries, keep it for the next bill",
                },
                {
                    "id": 35,
                    "speaker": 0,
                    "minute": 30,
                    "text": "Итого по интернету: Марко, Лео, Саша за, Катя против, я тоже за. Переходим на 500 Мбит с 1-го числа.",
                },
                {
                    "id": 36,
                    "speaker": 4,
                    "minute": 32,
                    "text": "Ладно, большинство так большинство",
                },
                {"id": 37, "speaker": 3, "minute": 33, "text": "🎉 finally"},
                {
                    "id": 38,
                    "speaker": 1,
                    "minute": 50,
                    "voice": "Hey guys, so the washing machine just stopped mid-cycle and it's full of water. There's an error on the display, E21 I think. I'll post a photo. Please don't use it for now.",
                },
                {
                    "id": 39,
                    "speaker": 1,
                    "minute": 52,
                    "image": "A washing machine display showing the error code E21, with water and clothes visible behind the door.",
                    "text": "This",
                },
                {
                    "id": 40,
                    "speaker": 2,
                    "minute": 55,
                    "text": "E21 is usually the drain pump",
                    "reply_to": 39,
                },
                {
                    "id": 41,
                    "speaker": 0,
                    "minute": 60,
                    "text": "Напишу хозяйке, это её машинка, пусть она чинит",
                },
                {
                    "id": 42,
                    "speaker": 3,
                    "minute": 62,
                    "text": "Laundromat round the corner charges 300 a load fyi",
                },
                {
                    "id": 43,
                    "speaker": 0,
                    "minute": 120,
                    "text": "Хозяйка ответила: мастер придёт в понедельник с 9 до 12, ремонт за её счёт. Кто будет дома?",
                },
                {
                    "id": 44,
                    "speaker": 2,
                    "minute": 125,
                    "text": "I can be home Monday morning, I work from home",
                    "reply_to": 43,
                },
                {"id": 45, "speaker": 0, "minute": 127, "text": "Спасибо, Саш!"},
                {
                    "id": 46,
                    "speaker": 4,
                    "minute": 140,
                    "voice": "Ребят, напоминаю, показания счётчиков воды надо передать до двадцать пятого. Я сфотографирую и передам сама, только напомните мне, если что.",
                },
                {"id": 47, "speaker": 3, "minute": 142, "text": "What did Katya say?"},
                {
                    "id": 48,
                    "speaker": 2,
                    "minute": 144,
                    "text": "Water meter readings are due by the 25th, she'll submit them herself",
                    "reply_to": 47,
                },
                {
                    "id": 49,
                    "speaker": 1,
                    "minute": 150,
                    "text": "Also heads up: I'm moving out at the end of the month 😢 New job in another city",
                },
                {"id": 50, "speaker": 4, "minute": 152, "text": "Ох, жаль 😢"},
                {"id": 51, "speaker": 3, "minute": 153, "text": "Noooo"},
                {
                    "id": 52,
                    "speaker": 0,
                    "minute": 155,
                    "text": "Жалко! Нужно будет кого-то найти на твою комнату",
                    "reply_to": 49,
                },
                {
                    "id": 53,
                    "speaker": 3,
                    "minute": 160,
                    "text": "My friend might be interested, I'll ask",
                },
                {
                    "id": 54,
                    "speaker": 1,
                    "minute": 170,
                    "text": "Does the landlord give back the deposit right away? It was 20,000",
                },
                {
                    "id": 55,
                    "speaker": 0,
                    "minute": 175,
                    "text": "Не знаю, раньше никто не съезжал. Спрошу",
                    "reply_to": 54,
                },
            ],
        },
        {
            "days_ago": 9,
            "messages": [
                {
                    "id": 56,
                    "speaker": 0,
                    "minute": 0,
                    "text": "Your tariff change to 500 Mbps is scheduled. Installation visit: the 9th, between 10:00 and 14:00. Monthly fee from the next billing period: 1 650.",
                    "forwarded": "Authored internet provider notice",
                },
                {
                    "id": 57,
                    "speaker": 3,
                    "minute": 5,
                    "text": "Who's home on the 9th?",
                    "reply_to": 56,
                },
                {"id": 58, "speaker": 4, "minute": 8, "text": "Я дома 9-го до обеда"},
                {
                    "id": 59,
                    "speaker": 2,
                    "minute": 10,
                    "text": "Katya's home on the 9th until lunch 👍",
                    "reply_to": 58,
                },
                {
                    "id": 60,
                    "speaker": 2,
                    "minute": 60,
                    "text": "Washing machine fixed! The technician replaced the drain pump, landlord paid, took 40 min",
                },
                {
                    "id": 61,
                    "speaker": 1,
                    "minute": 62,
                    "text": "Thank god, my laundry pile is enormous",
                },
                {
                    "id": 62,
                    "speaker": 0,
                    "minute": 80,
                    "text": "Хозяйка в субботу в 11 зайдёт посмотреть машинку и батареи",
                },
                {"id": 63, "speaker": 3, "minute": 85, "text": "Saturday 11, ok"},
                {
                    "id": 64,
                    "speaker": 0,
                    "minute": 120,
                    "text": "Поправка: хозяйка не в субботу, а в воскресенье в 12, у неё что-то поменялось.",
                },
                {"id": 65, "speaker": 1, "minute": 122, "text": "Sunday 12, got it"},
                {
                    "id": 66,
                    "speaker": 4,
                    "minute": 130,
                    "image": "Термометр на подоконнике показывает 17 °C, рядом холодная батарея.",
                    "text": "17 градусов в комнате 🥶",
                },
                {
                    "id": 67,
                    "speaker": 3,
                    "minute": 133,
                    "text": "Same here. When does heating come on??",
                    "reply_to": 66,
                },
                {
                    "id": 68,
                    "speaker": 0,
                    "minute": 140,
                    "voice": "Я позвонила в управляющую компанию, сказали, что отопление в нашем доме включат пятнадцатого числа, раньше никак, у них график по всему району.",
                },
                {
                    "id": 69,
                    "speaker": 2,
                    "minute": 143,
                    "text": "Heating comes on on the 15th, the building management says",
                },
                {
                    "id": 70,
                    "speaker": 3,
                    "minute": 145,
                    "text": "Ugh. Fine. Blankets then",
                },
                {
                    "id": 71,
                    "speaker": 1,
                    "minute": 160,
                    "text": "Any news about the deposit?",
                },
                {
                    "id": 72,
                    "speaker": 0,
                    "minute": 165,
                    "text": "Хозяйка сказала, обсудит при встрече в воскресенье",
                    "reply_to": 71,
                },
                {
                    "id": 73,
                    "speaker": 4,
                    "minute": 170,
                    "text": "Меняемся с Лео на третьей неделе: я беру ванную, он мусор",
                },
                {
                    "id": 74,
                    "speaker": 3,
                    "minute": 172,
                    "text": "Wait what did I agree to 😄",
                    "reply_to": 73,
                },
                {
                    "id": 75,
                    "speaker": 2,
                    "minute": 173,
                    "text": "Week 3 swap: Katya takes the bathroom, you take the bins",
                    "reply_to": 74,
                },
                {"id": 76, "speaker": 3, "minute": 175, "text": "Ah OK, deal"},
                {"id": 77, "speaker": 1, "minute": 190, "text": "Good night flat 🌙"},
            ],
        },
        {
            "days_ago": 5,
            "messages": [
                {
                    "id": 78,
                    "speaker": 0,
                    "minute": 0,
                    "text": "Встреча с хозяйкой: 1) депозит Марко вернёт после осмотра комнаты, в течение недели. 2) Батареи менять не будет. 3) Нового жильца хочет увидеть сама до заселения.",
                },
                {"id": 79, "speaker": 1, "minute": 5, "text": "Sasha, translate? 🙏"},
                {
                    "id": 80,
                    "speaker": 2,
                    "minute": 8,
                    "text": "1) Marco's deposit comes back within a week after she inspects the room. 2) She won't replace the radiators. 3) She wants to meet the new flatmate before they move in.",
                    "reply_to": 78,
                },
                {"id": 81, "speaker": 1, "minute": 10, "text": "Great, thanks both"},
                {
                    "id": 82,
                    "speaker": 3,
                    "minute": 40,
                    "text": "My friend wants to see the room! Can he come Thursday evening?",
                },
                {"id": 83, "speaker": 4, "minute": 45, "text": "Мне в четверг ок"},
                {"id": 84, "speaker": 0, "minute": 46, "text": "Ок, после 19"},
                {
                    "id": 85,
                    "speaker": 1,
                    "minute": 47,
                    "text": "After 7pm Thursday works, I'll tidy up 😅",
                    "reply_to": 84,
                },
                {
                    "id": 86,
                    "speaker": 4,
                    "minute": 90,
                    "text": "Показания воды передала: холодная 312, горячая 198 ✅",
                },
                {
                    "id": 87,
                    "speaker": 3,
                    "minute": 93,
                    "text": "What's that? 😅",
                    "reply_to": 86,
                },
                {
                    "id": 88,
                    "speaker": 2,
                    "minute": 95,
                    "text": "Water meter readings, she submitted them",
                    "reply_to": 87,
                },
                {
                    "id": 89,
                    "speaker": 3,
                    "minute": 120,
                    "text": "Internet guy came this morning, we're on 500 now, speed test says 470 🚀",
                },
                {"id": 90, "speaker": 4, "minute": 122, "text": "Ну ладно, быстро 😄"},
                {
                    "id": 91,
                    "speaker": 1,
                    "minute": 150,
                    "voice": "Hey everyone, so my last day here is the thirtieth. I'll leave the room clean, and I'm leaving the desk lamp and the shelf if anyone wants them. I'll pay my share of the October bills before I go.",
                },
                {
                    "id": 92,
                    "speaker": 0,
                    "minute": 155,
                    "text": "Спасибо, Марко! Лампу я заберу",
                },
                {"id": 93, "speaker": 3, "minute": 160, "text": "Shelf for me pls"},
                {
                    "id": 94,
                    "speaker": 0,
                    "minute": 200,
                    "text": "Кстати, кто-нибудь знает, оплатили интернет за этот месяц? Я не помню, переводила или нет",
                },
                {"id": 95, "speaker": 2, "minute": 205, "text": "Not sure tbh"},
                {
                    "id": 96,
                    "speaker": 4,
                    "minute": 210,
                    "text": "Я точно не переводила",
                },
            ],
        },
        {
            "days_ago": 2,
            "messages": [
                {
                    "id": 97,
                    "speaker": 3,
                    "minute": 0,
                    "text": "My friend loved the room! He wants it from the 1st, rent same as Marco's?",
                },
                {
                    "id": 98,
                    "speaker": 0,
                    "minute": 5,
                    "text": "Аренда та же, 18 000 за комнату плюс коммуналка",
                    "reply_to": 97,
                },
                {"id": 99, "speaker": 3, "minute": 7, "text": "What's коммуналка lol"},
                {
                    "id": 100,
                    "speaker": 2,
                    "minute": 8,
                    "text": "Utilities, split five ways like now",
                    "reply_to": 99,
                },
                {
                    "id": 101,
                    "speaker": 0,
                    "minute": 20,
                    "text": "Хозяйка хочет с ним встретиться в субботу в 14:00 тут",
                },
                {
                    "id": 102,
                    "speaker": 3,
                    "minute": 22,
                    "text": "Saturday 2pm, I'll tell him",
                    "reply_to": 101,
                },
                {
                    "id": 103,
                    "speaker": 4,
                    "minute": 60,
                    "voice": "Ребята, пришёл счёт за свет за октябрь, пока электронный, пять тысяч двести сорок. Это больше, чем в сентябре, наверное из-за обогревателей. По тысяче сорок восемь с человека.",
                },
                {
                    "id": 104,
                    "speaker": 1,
                    "minute": 62,
                    "text": "English summary anyone?",
                },
                {
                    "id": 105,
                    "speaker": 2,
                    "minute": 64,
                    "text": "October electricity 5,240, so 1,048 each. More than September, probably the heaters.",
                    "reply_to": 104,
                },
                {"id": 106, "speaker": 1, "minute": 66, "text": "Fair, sending now"},
                {
                    "id": 107,
                    "speaker": 0,
                    "minute": 90,
                    "text": "Марко, от сентября у тебя 36 в плюсе, так что с тебя 1 012",
                },
                {
                    "id": 108,
                    "speaker": 1,
                    "minute": 92,
                    "text": "Even better 😄 sent 1,012",
                    "reply_to": 107,
                },
                {
                    "id": 109,
                    "speaker": 3,
                    "minute": 120,
                    "text": "Farewell dinner for Marco on the 29th?",
                },
                {"id": 110, "speaker": 4, "minute": 122, "text": "Да!! Я испеку торт"},
                {
                    "id": 111,
                    "speaker": 0,
                    "minute": 123,
                    "text": "Я за, но где — дома или в кафе?",
                },
                {"id": 112, "speaker": 2, "minute": 125, "text": "Home is cheaper"},
                {"id": 113, "speaker": 3, "minute": 127, "text": "Let's decide later"},
                {"id": 114, "speaker": 1, "minute": 130, "text": "You guys 🥹"},
            ],
        },
    ],
}

# Twenty-four near-identical station notes: the distractor behind the published
# failure, where a clarification reply was cited together with one of them.
_STATIONS = [
    {
        "id": 25 + n,
        "speaker": (0, 1, 3)[n % 3],
        "minute": 1 + n,
        "text": f"Workshop station {n}: the sample board is ready for inspection, "
        "but the room booking is still a proposal. Please compare the blue label "
        "with the printed catalogue before moving any materials. If the label "
        "matches, leave the sample beside the window; if it differs, keep it on "
        "the shelf. Volunteers are available Wednesday after lunch, subject to "
        "the hall being confirmed. This is a planning note, not a report that "
        "the inspection has happened.",
    }
    for n in range(1, 25)
]

WORKSHOP = {
    "name": "workshop",
    "title": "Authored maker workshop / мастерская",
    "language": "mixed",
    "speakers": [
        "Authored Ilya",
        "Authored Greta",
        "Authored Vadim",
        "Authored Sam",
        "Authored Olya",
    ],
    "days": [
        {
            "days_ago": 23,
            "messages": [
                {
                    "id": 1,
                    "speaker": 0,
                    "minute": 0,
                    "text": "Всем привет! Hi all! Two things: the laser cutter is down, the lens cracked. And we need to plan the open day.",
                },
                {
                    "id": 2,
                    "speaker": 2,
                    "minute": 5,
                    "text": "Линзу я могу заказать, у меня есть поставщик. ~4 200 ₽",
                },
                {"id": 3, "speaker": 1, "minute": 7, "text": "What did Vadim say? 🙂"},
                {
                    "id": 4,
                    "speaker": 0,
                    "minute": 9,
                    "text": "He can order a new lens, around 4,200 roubles",
                    "reply_to": 3,
                },
                {
                    "id": 5,
                    "speaker": 1,
                    "minute": 10,
                    "text": "Do it, the fund can cover that",
                },
                {
                    "id": 6,
                    "speaker": 0,
                    "minute": 12,
                    "text": "Ок, Вадим, заказывай из фонда",
                },
                {
                    "id": 7,
                    "speaker": 3,
                    "minute": 30,
                    "text": "Open day: Saturday the 21st, 12 to 5?",
                },
                {"id": 8, "speaker": 4, "minute": 33, "text": "Я за 21-е"},
                {
                    "id": 9,
                    "speaker": 0,
                    "minute": 35,
                    "text": "Saturday 21st works for me",
                },
                {
                    "id": 10,
                    "speaker": 1,
                    "minute": 40,
                    "text": "I can run the 3D printing table. We need filament though, we're almost out",
                },
                {
                    "id": 11,
                    "speaker": 3,
                    "minute": 42,
                    "text": "PETG? I'd get 6 spools",
                    "reply_to": 10,
                },
                {
                    "id": 12,
                    "speaker": 1,
                    "minute": 45,
                    "text": "6 spools PETG at 1,300 each = 7,800. I'll order and the fund pays me back?",
                },
                {
                    "id": 13,
                    "speaker": 0,
                    "minute": 47,
                    "text": "Yes, fund pays",
                    "reply_to": 12,
                },
                {
                    "id": 14,
                    "speaker": 4,
                    "minute": 60,
                    "voice": "Ребят, я думаю, на дне открытых дверей надо сделать мастер-класс для детей, например, паять простые фонарики. Я могу провести, только нужны наборы, штук двадцать, это примерно по триста рублей.",
                },
                {
                    "id": 15,
                    "speaker": 2,
                    "minute": 62,
                    "text": "Хорошая идея",
                    "reply_to": 14,
                },
                {
                    "id": 16,
                    "speaker": 3,
                    "minute": 64,
                    "text": "Did Olya say kids soldering? Love it",
                },
                {
                    "id": 17,
                    "speaker": 0,
                    "minute": 66,
                    "text": "Наборы — 20 × 300 = 6 000. Это много для фонда, давайте подумаем",
                },
                {
                    "id": 18,
                    "speaker": 1,
                    "minute": 80,
                    "text": "Also, membership fee: should we raise it to 3,000? Rent went up",
                },
                {
                    "id": 19,
                    "speaker": 2,
                    "minute": 82,
                    "text": "Я против, многие уйдут",
                },
                {
                    "id": 20,
                    "speaker": 3,
                    "minute": 84,
                    "text": "Maybe 2,800 as a middle ground?",
                },
                {
                    "id": 21,
                    "speaker": 0,
                    "minute": 86,
                    "text": "Обсудим на общем собрании, не в чате",
                },
                {"id": 22, "speaker": 4, "minute": 100, "text": "👍"},
                {
                    "id": 23,
                    "speaker": 3,
                    "minute": 120,
                    "text": "Reminder for new people: no laser use without induction. Next induction is Tuesday 19:00 with me.",
                },
                {"id": 24, "speaker": 1, "minute": 122, "text": "👍"},
            ],
        },
        {
            "days_ago": 16,
            "messages": [
                {
                    "id": 25,
                    "speaker": 0,
                    "minute": 0,
                    "text": "Подготовка к дню открытых дверей: I'll post station notes for each table below, please read yours.",
                },
                *_STATIONS,
                {
                    "id": 50,
                    "speaker": 1,
                    "minute": 40,
                    "image": "A small blue circuit board and a large green circuit board side by side on a workbench, each with a paper label.",
                    "text": "Which board goes on the soldering table?",
                },
                {
                    "id": 51,
                    "speaker": 4,
                    "minute": 45,
                    "text": "Маленькая синяя, большая зелёная — для витрины",
                    "reply_to": 50,
                },
                {
                    "id": 52,
                    "speaker": 3,
                    "minute": 47,
                    "text": "In the station notes, is the sample board the small blue one?",
                },
                {
                    "id": 53,
                    "speaker": 0,
                    "minute": 50,
                    "text": "Yes, the small blue one. I can check its label, but I can't confirm the room booking yet.",
                    "reply_to": 52,
                },
                {
                    "id": 54,
                    "speaker": 2,
                    "minute": 60,
                    "voice": "Линзу заказал, придёт в среду, поставлю в четверг вечером. До четверга лазер не трогаем, он разобран.",
                },
                {
                    "id": 55,
                    "speaker": 1,
                    "minute": 62,
                    "text": "Vadim: lens Wednesday, installed Thursday evening, laser off until then, right?",
                },
                {
                    "id": 56,
                    "speaker": 2,
                    "minute": 63,
                    "text": "Yes, right",
                    "reply_to": 55,
                },
                {
                    "id": 57,
                    "speaker": 4,
                    "minute": 80,
                    "text": "Наборы для фонариков нашла по 250, а не по 300. 20 штук = 5 000",
                },
                {
                    "id": 58,
                    "speaker": 0,
                    "minute": 83,
                    "text": "Всё равно дорого. Может, 10 наборов?",
                    "reply_to": 57,
                },
                {
                    "id": 59,
                    "speaker": 4,
                    "minute": 85,
                    "text": "10 мало, дети в очереди будут стоять 😅",
                    "reply_to": 58,
                },
                {
                    "id": 60,
                    "speaker": 3,
                    "minute": 100,
                    "text": "Electricity in the basement units will be off on Wednesday from 10:00 to 16:00 for cable work.",
                    "forwarded": "Authored building management notice",
                },
                {
                    "id": 61,
                    "speaker": 1,
                    "minute": 102,
                    "text": "So no printing on Wednesday",
                    "reply_to": 60,
                },
                {
                    "id": 62,
                    "speaker": 0,
                    "minute": 110,
                    "text": "Booking update: the hall is still not confirmed, I'll call them Friday.",
                },
                {"id": 63, "speaker": 3, "minute": 112, "text": "ok"},
            ],
        },
        {
            "days_ago": 12,
            "messages": [
                {
                    "id": 64,
                    "speaker": 0,
                    "minute": 0,
                    "text": "Bad news: the hall is busy on the 21st. Open day moves to Saturday the 28th, same time, 12–17.",
                },
                {"id": 65, "speaker": 4, "minute": 3, "text": "28-е ок"},
                {"id": 66, "speaker": 1, "minute": 5, "text": "Fine by me"},
                {"id": 67, "speaker": 3, "minute": 7, "text": "28th 👍"},
                {
                    "id": 68,
                    "speaker": 0,
                    "minute": 20,
                    "text": "Зал на 28-е подтвердили, оплата 3 000 из фонда.",
                },
                {
                    "id": 69,
                    "speaker": 1,
                    "minute": 40,
                    "image": "Six spools of white PETG filament in a cardboard box on a shelf, with an invoice on top.",
                    "text": "Filament's here",
                },
                {
                    "id": 70,
                    "speaker": 0,
                    "minute": 42,
                    "text": "Перевёл тебе 7 800 из фонда",
                    "reply_to": 69,
                },
                {
                    "id": 71,
                    "speaker": 1,
                    "minute": 44,
                    "text": "Got it, thanks",
                    "reply_to": 70,
                },
                {
                    "id": 72,
                    "speaker": 2,
                    "minute": 60,
                    "text": "Лазер работает! Линзу поставил, тестовый рез ок",
                },
                {"id": 73, "speaker": 3, "minute": 62, "text": "🔥"},
                {
                    "id": 74,
                    "speaker": 4,
                    "minute": 80,
                    "voice": "Насчёт мастер-класса: я договорилась, наборы нам дадут со скидкой, пятнадцать штук за три тысячи. Беру пятнадцать, если никто не против.",
                },
                {
                    "id": 75,
                    "speaker": 0,
                    "minute": 85,
                    "text": "Не против, 3 000 из фонда ок",
                    "reply_to": 74,
                },
                {
                    "id": 76,
                    "speaker": 3,
                    "minute": 86,
                    "text": "Olya got 15 kits for 3,000? Nice",
                },
                {
                    "id": 77,
                    "speaker": 4,
                    "minute": 87,
                    "text": "Yes 🙂",
                    "reply_to": 76,
                },
                {
                    "id": 78,
                    "speaker": 1,
                    "minute": 100,
                    "text": "Who's got a red multimeter? Found it on the soldering table",
                },
                {
                    "id": 79,
                    "speaker": 1,
                    "minute": 101,
                    "image": "A red digital multimeter with a cracked screen corner lying on a soldering mat.",
                },
                {
                    "id": 80,
                    "speaker": 2,
                    "minute": 110,
                    "text": "Мой! Оставь в моём ящике, пожалуйста",
                    "reply_to": 79,
                },
                {
                    "id": 81,
                    "speaker": 1,
                    "minute": 112,
                    "text": "Done, it's in your drawer",
                },
                {
                    "id": 82,
                    "speaker": 3,
                    "minute": 130,
                    "text": "Induction on Tuesday: 3 new people signed up",
                },
                {"id": 83, "speaker": 0, "minute": 132, "text": "Отлично"},
                {
                    "id": 84,
                    "speaker": 4,
                    "minute": 140,
                    "text": "А кто будет на входе встречать гостей на дне открытых дверей?",
                },
                {
                    "id": 85,
                    "speaker": 3,
                    "minute": 145,
                    "text": "Not me, I'm on the laser demo",
                },
                {"id": 86, "speaker": 0, "minute": 147, "text": "Решим на неделе"},
            ],
        },
        {
            "days_ago": 7,
            "messages": [
                {
                    "id": 87,
                    "speaker": 3,
                    "minute": 0,
                    "voice": "Hi all, quick update on the induction: four people came on Tuesday, not three, and all of them passed, so they can use the laser now with someone else present.",
                },
                {"id": 88, "speaker": 0, "minute": 5, "text": "Great!"},
                {
                    "id": 89,
                    "speaker": 2,
                    "minute": 30,
                    "text": "Общее собрание по взносам когда?",
                },
                {
                    "id": 90,
                    "speaker": 0,
                    "minute": 33,
                    "text": "Предлагаю в четверг в 19:00",
                    "reply_to": 89,
                },
                {"id": 91, "speaker": 1, "minute": 35, "text": "Thursday 7pm ok"},
                {"id": 92, "speaker": 4, "minute": 36, "text": "Ок"},
                {
                    "id": 93,
                    "speaker": 3,
                    "minute": 38,
                    "text": "Can't make Thursday, count my vote for 2,800 please",
                },
                {
                    "id": 94,
                    "speaker": 0,
                    "minute": 60,
                    "text": "Встречать гостей будут Оля и Вадим, по очереди: Оля до 14:30, Вадим после.",
                },
                {"id": 95, "speaker": 2, "minute": 62, "text": "Ок", "reply_to": 94},
                {
                    "id": 96,
                    "speaker": 4,
                    "minute": 63,
                    "text": "Договорились",
                    "reply_to": 94,
                },
                {
                    "id": 97,
                    "speaker": 1,
                    "minute": 90,
                    "text": "Poster for the open day, thoughts?",
                },
                {
                    "id": 98,
                    "speaker": 1,
                    "minute": 91,
                    "image": "A poster reading 'Open Workshop Day, Saturday 28th, 12:00–17:00: soldering for kids, laser demo, 3D printing', with a drawing of a soldering iron.",
                },
                {
                    "id": 99,
                    "speaker": 3,
                    "minute": 93,
                    "text": "Love it",
                    "reply_to": 98,
                },
                {"id": 100, "speaker": 2, "minute": 95, "text": "Классно"},
                {
                    "id": 101,
                    "speaker": 0,
                    "minute": 120,
                    "text": "Кто может распечатать 30 плакатов? В копицентре по 40 ₽",
                },
                {
                    "id": 102,
                    "speaker": 3,
                    "minute": 122,
                    "text": "I'll print them, 30 × 40 = 1,200, fund?",
                    "reply_to": 101,
                },
                {
                    "id": 103,
                    "speaker": 0,
                    "minute": 123,
                    "text": "Yes, fund",
                    "reply_to": 102,
                },
            ],
        },
        {
            "days_ago": 3,
            "messages": [
                {
                    "id": 104,
                    "speaker": 0,
                    "minute": 0,
                    "text": "Итоги собрания: взнос поднимаем до 2 800 с первого числа. 5 за, 2 против.",
                },
                {
                    "id": 105,
                    "speaker": 1,
                    "minute": 3,
                    "text": "Summary in English? 🙂",
                },
                {
                    "id": 106,
                    "speaker": 4,
                    "minute": 5,
                    "text": "Fee goes up to 2,800 from the 1st, 5 votes for, 2 against",
                    "reply_to": 105,
                },
                {"id": 107, "speaker": 3, "minute": 7, "text": "Fair"},
                {
                    "id": 108,
                    "speaker": 2,
                    "minute": 30,
                    "voice": "Ребят, я в субботу двадцать восьмого буду только с часу, утром у меня дела. Так что на входе после половины третьего — да, а помогать с установкой с утра не смогу.",
                },
                {
                    "id": 109,
                    "speaker": 1,
                    "minute": 33,
                    "text": "Vadim from 13:00 on the 28th, can't help setting up",
                },
                {
                    "id": 110,
                    "speaker": 0,
                    "minute": 35,
                    "text": "Кто тогда поможет со столами с утра? Нужно двое к 11:00",
                },
                {"id": 111, "speaker": 3, "minute": 37, "text": "I'll be there at 11"},
                {"id": 112, "speaker": 0, "minute": 40, "text": "Нужен ещё один"},
                {
                    "id": 113,
                    "speaker": 4,
                    "minute": 60,
                    "text": "Фонарики-наборы пришли, 15 штук ✅",
                },
                {
                    "id": 114,
                    "speaker": 1,
                    "minute": 90,
                    "text": "Will there be coffee for visitors?",
                },
                {
                    "id": 115,
                    "speaker": 0,
                    "minute": 92,
                    "text": "Хороший вопрос 😅",
                    "reply_to": 114,
                },
                {
                    "id": 116,
                    "speaker": 3,
                    "minute": 120,
                    "text": "See you all on the 28th!",
                },
            ],
        },
    ],
}

GROUPS = [GARDEN, CHOIR, SCHOOL, DACHA, FLAT, WORKSHOP]

# Labels are authored before any run. A citation is True when the message itself,
# read with the message it replies to, states, shows or clearly implies something
# the claim reports; "supported" is whether the cited messages together support
# everything the claim says. "trap" names the planted error pattern, if any.
CLAIMS = [
    {
        "group": "garden",
        "kind": "topic",
        "language": "en",
        "title": "Compost delivery",
        "text": "The depot delivered only 2 of the 3 cubic metres ordered. The "
        "missing metre came on Wednesday, and the price was reduced to €120.",
        "citations": {27: True, 31: True, 65: True},
        "supported": True,
        "trap": None,
    },
    {
        "group": "garden",
        "kind": "topic",
        "language": "en",
        "title": "Plot fees",
        "text": "Plot fees are €35 per plot, the same as last year, and are due by "
        "the 15th.",
        "citations": {1: True, 4: True},
        "supported": True,
        "trap": "reply_needed",
    },
    {
        "group": "garden",
        "kind": "topic",
        "language": "en",
        "title": "Aphids on bed 7",
        "text": "Marta found aphids on bed 7 and posted a photo of an affected bean "
        "leaf.",
        "citations": {20: False, 75: True},
        "supported": True,
        "trap": "near_duplicate",
    },
    {
        "group": "garden",
        "kind": "statement",
        "language": "en",
        "title": None,
        "text": "Noor has the shed key now; Eli handed it to Noor.",
        "citations": {33: False, 97: True, 98: True},
        "supported": True,
        "trap": "same_subject",
    },
    {
        "group": "garden",
        "kind": "topic",
        "language": "en",
        "title": "Working party date",
        "text": "The working party, provisionally set for Saturday the 14th, moved "
        "to Sunday the 15th at 10:00 because the skip could only come on Sunday.",
        "citations": {43: True, 49: True},
        "supported": True,
        "trap": None,
    },
    {
        "group": "garden",
        "kind": "statement",
        "language": "en",
        "title": None,
        "text": "The working party is on Saturday the 14th.",
        "citations": {43: True, 49: False},
        "supported": False,
        "trap": "contradicted",
    },
    {
        "group": "garden",
        "kind": "statement",
        "language": "en",
        "title": None,
        "text": "Eli agreed to take the Sunday watering every other week.",
        "citations": {91: True, 92: False},
        "supported": False,
        "trap": "overstated",
    },
    {
        "group": "garden",
        "kind": "statement",
        "language": "en",
        "title": None,
        "text": "Marta offered to bring spare tomato seedlings to the working "
        "party, two per plot.",
        "citations": {52: False, 53: True},
        "supported": False,
        "trap": "wrong_person",
    },
    {
        "group": "garden",
        "kind": "topic",
        "language": "en",
        "title": "Working party recap",
        "text": "Seven people came, spread all three cubic metres of compost, "
        "cleared the path by plots 9–12 and fixed the water butt.",
        "citations": {73: True},
        "supported": False,
        "trap": "missing_part",
    },
    {
        "group": "garden",
        "kind": "topic",
        "language": "ru",
        "title": "Отключение воды",
        "text": "В четверг с 8:00 до 14:00 на участках отключат воду, поэтому "
        "предложили полить в среду вечером.",
        "citations": {82: True, 83: True},
        "supported": True,
        "trap": None,
    },
    {
        "group": "garden",
        "kind": "topic",
        "language": "ru",
        "title": "Участок 4",
        "text": "Владелец участка 4 отказывается от него; участок предложат "
        "первому из двух человек в листе ожидания, а при отказе — второму.",
        "citations": {101: True, 103: True},
        "supported": True,
        "trap": None,
    },
    {
        "group": "garden",
        "kind": "statement",
        "language": "ru",
        "title": None,
        "text": "Бочку для воды снова можно наполнять доверху: кран заменили.",
        "citations": {5: False, 38: False},
        "supported": False,
        "trap": "same_subject",
    },
    {
        "group": "garden",
        "kind": "topic",
        "language": "en",
        "title": "Harvest share box",
        "text": "The group agreed that Tobias will build a harvest share box from "
        "the old pallets.",
        "citations": {87: True, 89: False},
        "supported": False,
        "trap": "overstated",
    },
]

CLAIMS += [
    {
        "group": "choir",
        "kind": "topic",
        "language": "en",
        "title": "Concert date",
        "text": "The spring concert moved from Saturday the 12th to Friday the 11th "
        "because the hall was double-booked. Doors open at 19:00 and singing "
        "starts at 19:30.",
        "citations": {49: True, 55: True},
        "supported": True,
        "trap": None,
    },
    {
        "group": "choir",
        "kind": "topic",
        "language": "en",
        "title": "Lullaby solo",
        "text": "Owen had to give up the lullaby solo because of a throat "
        "infection, and Lise agreed to sing it instead.",
        "citations": {75: True, 78: True, 79: True},
        "supported": True,
        "trap": "reply_needed",
    },
    {
        "group": "choir",
        "kind": "statement",
        "language": "en",
        "title": None,
        "text": "Owen sings the lullaby solo.",
        "citations": {26: True, 75: False},
        "supported": False,
        "trap": "contradicted",
    },
    {
        "group": "choir",
        "kind": "topic",
        "language": "en",
        "title": "Score copy 7",
        "text": "Copy 7 of the new score is missing pages 3 and 4, and Felix will "
        "reprint them.",
        "citations": {19: False, 28: True, 29: True},
        "supported": True,
        "trap": "near_duplicate",
    },
    {
        "group": "choir",
        "kind": "statement",
        "language": "en",
        "title": None,
        "text": "Copy 7 of the score is missing pages.",
        "citations": {19: False, 23: False},
        "supported": False,
        "trap": "near_duplicate",
    },
    {
        "group": "choir",
        "kind": "topic",
        "language": "en",
        "title": "Breathing mark",
        "text": "Altos should breathe at bar 32 in the second piece.",
        "citations": {32: True, 33: False},
        "supported": False,
        "trap": "contradicted",
    },
    {
        "group": "choir",
        "kind": "statement",
        "language": "en",
        "title": None,
        "text": "The choir decided to film the concert on video.",
        "citations": {38: True, 41: False},
        "supported": False,
        "trap": "overstated",
    },
    {
        "group": "choir",
        "kind": "statement",
        "language": "en",
        "title": None,
        "text": "The hall has a ramp at the side door.",
        "citations": {104: False, 105: False},
        "supported": False,
        "trap": "same_subject",
    },
    {
        "group": "choir",
        "kind": "statement",
        "language": "en",
        "title": None,
        "text": "Hanna offered to sign for the hall's urn on the concert day.",
        "citations": {61: True, 91: False},
        "supported": False,
        "trap": "wrong_person",
    },
    {
        "group": "choir",
        "kind": "topic",
        "language": "ru",
        "title": "Билеты",
        "text": "Билеты: 12 € для взрослых, 8 € льготные, дети до 12 лет "
        "бесплатно; продано уже 88 билетов.",
        "citations": {4: True, 6: True, 86: False},
        "supported": False,
        "trap": "near_duplicate",
    },
    {
        "group": "choir",
        "kind": "statement",
        "language": "ru",
        "title": None,
        "text": "Чай в антракте берёт на себя Ханна.",
        "citations": {90: False, 91: False},
        "supported": False,
        "trap": "contradicted",
    },
    {
        "group": "choir",
        "kind": "topic",
        "language": "en",
        "title": "Interval refreshments",
        "text": "Kai will run coffee and lemon cake at the interval, and the hall "
        "lends its urn for free.",
        "citations": {42: True, 90: True},
        "supported": False,
        "trap": "missing_part",
    },
]

CLAIMS += [
    {
        "group": "school",
        "kind": "topic",
        "language": "ru",
        "title": "Сбор на подарок",
        "text": "На подарок учительнице решили сдавать по 400 ₽ вместо 500: "
        "сертификата на 10 000 хватает. Тем, кто уже сдал 500, Вера предложила "
        "вернуть по 100 или оставить их на цветы.",
        "citations": {27: False, 33: True, 38: True},
        "supported": True,
        "trap": "same_subject",
    },
    {
        "group": "school",
        "kind": "topic",
        "language": "ru",
        "title": "Сопровождающие на экскурсии",
        "text": "На экскурсию 14-го детей сопровождают Тимур и Ксения: Света не "
        "смогла из-за смены на работе.",
        "citations": {13: False, 52: True, 54: True},
        "supported": True,
        "trap": "same_subject",
    },
    {
        "group": "school",
        "kind": "statement",
        "language": "ru",
        "title": None,
        "text": "Экскурсию сопровождают Тимур и Света.",
        "citations": {14: True, 54: False},
        "supported": False,
        "trap": "contradicted",
    },
    {
        "group": "school",
        "kind": "topic",
        "language": "ru",
        "title": "Контрольная по математике",
        "text": "Контрольная по математике будет в пятницу, 15-го, а не в "
        "четверг. Нужно повторить таблицу умножения на 7 и 8.",
        "citations": {59: False, 63: True, 97: True},
        "supported": True,
        "trap": "near_duplicate",
    },
    {
        "group": "school",
        "kind": "statement",
        "language": "ru",
        "title": None,
        "text": "Контрольная по математике — в четверг.",
        "citations": {49: True, 63: False},
        "supported": False,
        "trap": "contradicted",
    },
    {
        "group": "school",
        "kind": "statement",
        "language": "ru",
        "title": None,
        "text": "ДЗ на четверг: русский язык упр. 152, чтение стр. 72–75.",
        "citations": {18: False, 59: True},
        "supported": True,
        "trap": "near_duplicate",
    },
    {
        "group": "school",
        "kind": "topic",
        "language": "en",
        "title": "Parents' meeting",
        "text": "The parents' meeting moved from Wednesday 18:30 to Thursday "
        "19:00. It is in room 241.",
        "citations": {23: False, 24: True, 25: True, 40: True},
        "supported": True,
        "trap": "same_subject",
    },
    {
        "group": "school",
        "kind": "statement",
        "language": "ru",
        "title": None,
        "text": "На празднике будет фотографировать учитель информатики.",
        "citations": {100: True, 101: False},
        "supported": False,
        "trap": "overstated",
    },
    {
        "group": "school",
        "kind": "statement",
        "language": "ru",
        "title": None,
        "text": "Синий мешок с жирафом забрали хозяева.",
        "citations": {28: False, 73: False, 94: False},
        "supported": False,
        "trap": "same_subject",
    },
    {
        "group": "school",
        "kind": "topic",
        "language": "ru",
        "title": "Чаепитие на празднике",
        "text": "На чаепитие 28-го нужны трое родителей: Ксения печёт пирог, "
        "Игорь приносит чай и стаканы, а третьего пока не нашли.",
        "citations": {49: True, 65: False, 91: True, 102: True},
        "supported": True,
        "trap": "reply_needed",
    },
    {
        "group": "school",
        "kind": "topic",
        "language": "en",
        "title": "Teacher's gift",
        "text": "The class bought a 10,000-rouble bookshop gift certificate and "
        "flowers for 1,200 roubles, spending exactly what had been collected. All "
        "26 families have now paid.",
        "citations": {83: False, 85: True, 88: True},
        "supported": False,
        "trap": "near_duplicate",
    },
    {
        "group": "school",
        "kind": "statement",
        "language": "ru",
        "title": None,
        "text": "В конце экскурсии дети пекли булочки, вернулись в 14:35, а "
        "обедали прямо в музее.",
        "citations": {77: True, 80: True},
        "supported": False,
        "trap": "missing_part",
    },
]

CLAIMS += [
    {
        "group": "dacha",
        "kind": "topic",
        "language": "ru",
        "title": "Время выезда",
        "text": "Сбор в субботу перенесли с 8:00 на 7:30: Паше нужно вернуть "
        "газель на базу к 15:00.",
        "citations": {41: True, 42: True, 43: False},
        "supported": True,
        "trap": "same_subject",
    },
    {
        "group": "dacha",
        "kind": "topic",
        "language": "ru",
        "title": "Коробка №7",
        "text": "Коробку №7 с документами и зарядками везут в машине, а не в газели.",
        "citations": {31: False, 37: True},
        "supported": True,
        "trap": "near_duplicate",
    },
    {
        "group": "dacha",
        "kind": "statement",
        "language": "en",
        "title": None,
        "text": "Box 7 holds the documents and chargers and travels in the car.",
        "citations": {31: False, 33: False},
        "supported": False,
        "trap": "near_duplicate",
    },
    {
        "group": "dacha",
        "kind": "topic",
        "language": "ru",
        "title": "Холодильник",
        "text": "Старый холодильник решили не везти на дачу и продали за 4 000 ₽ "
        "вместо запрошенных 5 000.",
        "citations": {39: True, 53: True},
        "supported": True,
        "trap": None,
    },
    {
        "group": "dacha",
        "kind": "statement",
        "language": "ru",
        "title": None,
        "text": "Холодильник продали за 5 000 ₽.",
        "citations": {39: True, 53: False},
        "supported": False,
        "trap": "contradicted",
    },
    {
        "group": "dacha",
        "kind": "topic",
        "language": "ru",
        "title": "Расчёт с Пашей",
        "text": "Лёша перевёл Паше 4 900 ₽: 3 500 за бензин и 1 400 за ремни.",
        "citations": {46: False, 58: True, 90: True},
        "supported": True,
        "trap": "same_subject",
    },
    {
        "group": "dacha",
        "kind": "statement",
        "language": "ru",
        "title": None,
        "text": "Ремни для крепления купил Дима.",
        "citations": {46: False, 58: True},
        "supported": False,
        "trap": "wrong_person",
    },
    {
        "group": "dacha",
        "kind": "statement",
        "language": "ru",
        "title": None,
        "text": "Старый ковёр выставили на барахолку.",
        "citations": {48: False, 93: True},
        "supported": False,
        "trap": "overstated",
    },
    {
        "group": "dacha",
        "kind": "topic",
        "language": "ru",
        "title": "Шашлыки",
        "text": "Наташа пригласила всех с семьями на шашлыки на дачу в субботу, "
        "2-го числа.",
        "citations": {98: True},
        "supported": False,
        "trap": "missing_part",
    },
    {
        "group": "dacha",
        "kind": "topic",
        "language": "ru",
        "title": "Ключ от калитки",
        "text": "Ключ от калитки у соседки из дома 12; в день переезда она "
        "отдала его Наташе.",
        "citations": {22: True, 80: True},
        "supported": True,
        "trap": "reply_needed",
    },
    {
        "group": "dacha",
        "kind": "statement",
        "language": "en",
        "title": None,
        "text": "The washing machine is already connected at the dacha.",
        "citations": {81: False, 88: False, 89: False},
        "supported": False,
        "trap": "same_subject",
    },
]

CLAIMS += [
    {
        "group": "flat",
        "kind": "topic",
        "language": "ru",
        "title": "Свет за сентябрь",
        "text": "Счёт за свет за сентябрь — 4 680 ₽, по 936 с человека; сначала "
        "Аня по ошибке написала 4 860.",
        "citations": {1: True, 31: True, 103: False},
        "supported": True,
        "trap": "near_duplicate",
    },
    {
        "group": "flat",
        "kind": "statement",
        "language": "en",
        "title": None,
        "text": "September's electricity share is 972 roubles per person.",
        "citations": {1: True, 31: False},
        "supported": False,
        "trap": "contradicted",
    },
    {
        "group": "flat",
        "kind": "topic",
        "language": "en",
        "title": "Internet upgrade",
        "text": "The flat voted to switch to the 500 Mbps plan for 1,650 a month; "
        "Katya voted against.",
        "citations": {7: True, 12: False, 35: True},
        "supported": True,
        "trap": "same_subject",
    },
    {
        "group": "flat",
        "kind": "statement",
        "language": "ru",
        "title": None,
        "text": "Стиральную машину починил Саша.",
        "citations": {40: False, 60: True},
        "supported": False,
        "trap": "wrong_person",
    },
    {
        "group": "flat",
        "kind": "topic",
        "language": "en",
        "title": "Washing machine",
        "text": "The washing machine stopped with error E21; the landlord paid for "
        "a technician, who replaced the drain pump.",
        "citations": {38: True, 42: False, 60: True},
        "supported": True,
        "trap": "same_subject",
    },
    {
        "group": "flat",
        "kind": "statement",
        "language": "en",
        "title": None,
        "text": "Leo's friend will take Marco's room from the 1st.",
        "citations": {97: True, 101: False},
        "supported": False,
        "trap": "overstated",
    },
    {
        "group": "flat",
        "kind": "topic",
        "language": "ru",
        "title": "Визит хозяйки",
        "text": "Хозяйка придёт в субботу в 11 и посмотрит машинку и батареи.",
        "citations": {62: True, 64: False},
        "supported": False,
        "trap": "contradicted",
    },
    {
        "group": "flat",
        "kind": "topic",
        "language": "ru",
        "title": "Уборка, неделя 3",
        "text": "На третьей неделе Катя и Лео поменялись: Катя убирает ванную, "
        "Лео выносит мусор.",
        "citations": {26: False, 73: True},
        "supported": True,
        "trap": "near_duplicate",
    },
    {
        "group": "flat",
        "kind": "statement",
        "language": "en",
        "title": None,
        "text": "In week 3 Leo cleans the bathroom and Katya takes the bins.",
        "citations": {21: True, 73: False},
        "supported": False,
        "trap": "contradicted",
    },
    {
        "group": "flat",
        "kind": "statement",
        "language": "ru",
        "title": None,
        "text": "Интернет за этот месяц уже оплачен.",
        "citations": {36: False, 94: False, 96: False},
        "supported": False,
        "trap": "same_subject",
    },
    {
        "group": "flat",
        "kind": "topic",
        "language": "mixed",
        "title": "Marco's deposit",
        "text": "Marco's deposit (20,000): хозяйка вернёт его в течение недели "
        "после осмотра комнаты.",
        "citations": {54: True, 72: False, 78: True},
        "supported": True,
        "trap": "same_subject",
    },
]

CLAIMS += [
    {
        # The published failure: a correct clarification reply cited together
        # with an unrelated, near-identical station note.
        "group": "workshop",
        "kind": "topic",
        "language": "en",
        "title": "Which board",
        "text": "Olya clarified that the small blue board goes on the soldering "
        "table and the large green one is for the display case.",
        "citations": {41: False, 51: True},
        "supported": True,
        "trap": "near_duplicate",
    },
    {
        "group": "workshop",
        "kind": "topic",
        "language": "en",
        "title": "Sample board",
        "text": "Ilya confirmed that the sample board in the station notes is the "
        "small blue one.",
        "citations": {44: False, 53: True},
        "supported": True,
        "trap": "near_duplicate",
    },
    {
        "group": "workshop",
        "kind": "statement",
        "language": "en",
        "title": None,
        "text": "The small blue board has passed its label inspection.",
        "citations": {29: False, 53: False},
        "supported": False,
        "trap": "overstated",
    },
    {
        "group": "workshop",
        "kind": "topic",
        "language": "ru",
        "title": "Лазерный резак",
        "text": "Лазерный резак не работал из-за треснувшей линзы; Вадим поставил "
        "новую линзу, и тестовый рез прошёл нормально.",
        "citations": {1: True, 72: True},
        "supported": True,
        "trap": None,
    },
    {
        "group": "workshop",
        "kind": "statement",
        "language": "ru",
        "title": None,
        "text": "День открытых дверей — в субботу, 21-го.",
        "citations": {8: True, 64: False},
        "supported": False,
        "trap": "contradicted",
    },
    {
        "group": "workshop",
        "kind": "topic",
        "language": "ru",
        "title": "Наборы для пайки",
        "text": "Оля договорилась о 15 наборах для пайки фонариков за 3 000 ₽, и "
        "наборы уже пришли.",
        "citations": {17: False, 74: True, 113: True},
        "supported": True,
        "trap": "same_subject",
    },
    {
        "group": "workshop",
        "kind": "statement",
        "language": "ru",
        "title": None,
        "text": "Взнос решили поднять до 3 000 ₽.",
        "citations": {18: True, 21: False},
        "supported": False,
        "trap": "overstated",
    },
    {
        "group": "workshop",
        "kind": "topic",
        "language": "en",
        "title": "Membership fee",
        "text": "The workshop voted 5 to 2 to raise the membership fee to 2,800 "
        "from the 1st.",
        "citations": {105: False, 106: True},
        "supported": True,
        "trap": "same_subject",
    },
    {
        "group": "workshop",
        "kind": "statement",
        "language": "en",
        "title": None,
        "text": "Sam offered to run the soldering workshop for kids.",
        "citations": {14: True, 16: False},
        "supported": False,
        "trap": "wrong_person",
    },
    {
        "group": "workshop",
        "kind": "topic",
        "language": "ru",
        "title": "Встреча гостей",
        "text": "Гостей на входе встречают по очереди: Оля до 14:30, Вадим после; "
        "утром Вадим занят и приедет к 13:00.",
        "citations": {84: False, 94: True, 108: True},
        "supported": True,
        "trap": "same_subject",
    },
    {
        "group": "workshop",
        "kind": "statement",
        "language": "ru",
        "title": None,
        "text": "Кофе для гостей на дне открытых дверей обеспечит фонд.",
        "citations": {68: False, 115: False},
        "supported": False,
        "trap": "same_subject",
    },
]

# Relevant IDs are the messages whose content an answer needs; a reply's parent
# reaches the writer as context. Unanswerable questions have no relevant IDs.
QUESTIONS = [
    {
        "group": "garden",
        "language": "en",
        "question": "How much is the plot fee this season, and has it changed since last year?",
        "relevant": [1, 4],
        "answer": "€35 per plot, the same as last year (messages 1 and 4).",
        "answerable": True,
        "kind": "number",
    },
    {
        "group": "garden",
        "language": "en",
        "question": "What did the compost cost in the end?",
        "relevant": [31],
        "answer": "€120 in total after the depot lowered the price (message 31).",
        "answerable": True,
        "kind": "change",
    },
    {
        "group": "garden",
        "language": "en",
        "question": "Who is holding the shed key at the moment?",
        "relevant": [97, 98],
        "answer": "Noor; Eli handed it over (messages 97 and 98).",
        "answerable": True,
        "kind": "change",
    },
    {
        "group": "garden",
        "language": "en",
        "question": "When did the working party actually take place?",
        "relevant": [49],
        "answer": "Sunday the 15th, starting at 10:00 (message 49).",
        "answerable": True,
        "kind": "change",
    },
    {
        "group": "garden",
        "language": "en",
        "question": "How many tomato seedlings did Tobias offer, and how many per plot?",
        "relevant": [53],
        "answer": "24 spare seedlings, mostly cherry tomatoes, two per plot (message 53).",
        "answerable": True,
        "kind": "number",
    },
    {
        "group": "garden",
        "language": "en",
        "question": "What is Marta bringing for the volunteers on the work day?",
        "relevant": [52],
        "answer": "A big flask of tea, biscuits and two spare pairs of gloves (message 52).",
        "answerable": True,
        "kind": "fact",
    },
    {
        "group": "garden",
        "language": "en",
        "question": "How did the group decide to deal with the bugs on bed 7?",
        "relevant": [75, 80],
        "answer": "Soapy water on bed 7 every two days for a week, applied by Marta (messages 75 and 80).",
        "answerable": True,
        "kind": "decision",
    },
    {
        "group": "garden",
        "language": "en",
        "question": "When will the standpipe water be off?",
        "relevant": [82],
        "answer": "Thursday from 08:00 to 14:00 (message 82).",
        "answerable": True,
        "kind": "date",
    },
    {
        "group": "garden",
        "language": "en",
        "question": "How many plots have paid their fee so far?",
        "relevant": [85],
        "answer": "11 of 12; only plot 4 had not paid (message 85).",
        "answerable": True,
        "kind": "change",
    },
    {
        "group": "garden",
        "language": "en",
        "question": "Who will do the Sunday watering?",
        "relevant": [],
        "answer": None,
        "answerable": False,
        "kind": "open",
    },
    {
        "group": "garden",
        "language": "en",
        "question": "Who are the two people on the waiting list for plot 4?",
        "relevant": [],
        "answer": None,
        "answerable": False,
        "kind": "person",
    },
    {
        "group": "garden",
        "language": "en",
        "question": "How much did the new tap for the water butt cost?",
        "relevant": [],
        "answer": None,
        "answerable": False,
        "kind": "number",
    },
    {
        "group": "garden",
        "language": "en",
        "question": "Is it OK to fill the rain barrel completely again?",
        "relevant": [55, 56, 57],
        "answer": "Yes; after the new tap was fitted Tobias said it can be filled all the way (messages 56 and 57).",
        "answerable": True,
        "kind": "change",
    },
    {
        "group": "garden",
        "language": "en",
        "question": "Who pays for small tool purchases like the new hoe?",
        "relevant": [35, 37],
        "answer": "The kitty covers anything under €25 if it is written in the book; the hoe cost €18 (messages 35 and 37).",
        "answerable": True,
        "kind": "fact",
    },
    {
        "group": "garden",
        "language": "ru",
        "question": "Сколько человек пришло на субботник и что успели сделать?",
        "relevant": [73],
        "answer": "Семь человек: разбросали все 3 кубометра компоста, расчистили дорожку у участков 9–12, рассаду Тобиаса разобрали (сообщение 73).",
        "answerable": True,
        "kind": "number",
    },
    {
        "group": "garden",
        "language": "ru",
        "question": "Кто построит ящик для обмена урожаем и когда?",
        "relevant": [104, 105],
        "answer": "Тобиас, в эти выходные, после того как комитет одобрил ящик (сообщения 104 и 105).",
        "answerable": True,
        "kind": "person",
    },
    {
        "group": "garden",
        "language": "ru",
        "question": "Где хранится книга, в которую записывают расходы из кассы?",
        "relevant": [],
        "answer": None,
        "answerable": False,
        "kind": "fact",
    },
    {
        "group": "choir",
        "language": "en",
        "question": "When is the spring concert now, and at what time does it start?",
        "relevant": [55],
        "answer": "Friday the 11th; doors at 19:00, start at 19:30 (message 55).",
        "answerable": True,
        "kind": "change",
    },
    {
        "group": "choir",
        "language": "en",
        "question": "Why did the concert date change?",
        "relevant": [49],
        "answer": "The hall was double-booked for Saturday the 12th and offered Friday the 11th instead (message 49).",
        "answerable": True,
        "kind": "fact",
    },
    {
        "group": "choir",
        "language": "en",
        "question": "Who is singing the lullaby solo?",
        "relevant": [75, 80],
        "answer": "Lise, because Owen has a throat infection (messages 75 and 80).",
        "answerable": True,
        "kind": "change",
    },
    {
        "group": "choir",
        "language": "en",
        "question": "What does the new poster say about ticket prices?",
        "relevant": [81],
        "answer": "€12, €8 for concessions, under 12s free (message 81).",
        "answerable": True,
        "kind": "number",
    },
    {
        "group": "choir",
        "language": "en",
        "question": "How many tickets have been sold so far?",
        "relevant": [98],
        "answer": "88 (message 98).",
        "answerable": True,
        "kind": "change",
    },
    {
        "group": "choir",
        "language": "en",
        "question": "Where should the altos take a breath in the second piece?",
        "relevant": [32, 33],
        "answer": "At bar 23, not bar 32 (messages 32 and 33).",
        "answerable": True,
        "kind": "change",
    },
    {
        "group": "choir",
        "language": "en",
        "question": "What is the plan for the warm-up on concert day?",
        "relevant": [92],
        "answer": "Meet at six in room 6, ten minutes of breathing, the first piece once through, then walk to the hall together (message 92).",
        "answerable": True,
        "kind": "fact",
    },
    {
        "group": "choir",
        "language": "en",
        "question": "Will the concert be recorded?",
        "relevant": [68, 88],
        "answer": "Audio only, shared within the choir; no video (messages 68 and 88).",
        "answerable": True,
        "kind": "decision",
    },
    {
        "group": "choir",
        "language": "en",
        "question": "What are the conditions for borrowing the hall's hot water urn?",
        "relevant": [60],
        "answer": "It is free, but someone must sign for it on the day and return it clean by 10 pm (message 60).",
        "answerable": True,
        "kind": "fact",
    },
    {
        "group": "choir",
        "language": "en",
        "question": "Which room are rehearsals in now?",
        "relevant": [56, 58],
        "answer": "Room 6 on the first floor, past the lift, while room 4 is painted (messages 56 and 58).",
        "answerable": True,
        "kind": "change",
    },
    {
        "group": "choir",
        "language": "en",
        "question": "How much money is left in the choir's kitty?",
        "relevant": [95],
        "answer": "€208 after the posters (message 95).",
        "answerable": True,
        "kind": "change",
    },
    {
        "group": "choir",
        "language": "en",
        "question": "Who will serve tea at the interval?",
        "relevant": [],
        "answer": None,
        "answerable": False,
        "kind": "open",
    },
    {
        "group": "choir",
        "language": "en",
        "question": "Does the hall have a ramp at the side door?",
        "relevant": [],
        "answer": None,
        "answerable": False,
        "kind": "open",
    },
    {
        "group": "choir",
        "language": "en",
        "question": "What is the title of the second piece the altos sing?",
        "relevant": [],
        "answer": None,
        "answerable": False,
        "kind": "fact",
    },
    {
        "group": "choir",
        "language": "ru",
        "question": "Сколько новых шарфов сшила Ханна?",
        "relevant": [83],
        "answer": "Все 30 новых красных шарфов (сообщение 83).",
        "answerable": True,
        "kind": "number",
    },
    {
        "group": "choir",
        "language": "ru",
        "question": "Кто отвечает за кофе и выпечку в антракте?",
        "relevant": [90],
        "answer": "Кай: кофе и лимонный кекс (сообщение 90).",
        "answerable": True,
        "kind": "person",
    },
    {
        "group": "choir",
        "language": "ru",
        "question": "Где на сцене стоит Lise во время концерта?",
        "relevant": [100, 102],
        "answer": "Впереди по центру только на время соло, потом возвращается в свой ряд (сообщения 100 и 102).",
        "answerable": True,
        "kind": "fact",
    },
]

QUESTIONS += [
    {
        "group": "school",
        "language": "ru",
        "question": "Сколько сдаём на подарок учительнице?",
        "relevant": [38],
        "answer": "По 400 ₽; сначала собирали по 500 (сообщение 38).",
        "answerable": True,
        "kind": "change",
    },
    {
        "group": "school",
        "language": "ru",
        "question": "Сколько стоит экскурсия в музей хлеба и из чего складывается сумма?",
        "relevant": [7],
        "answer": "950 ₽: детский билет 450 и автобус 500 (голосовое, сообщение 7).",
        "answerable": True,
        "kind": "number",
    },
    {
        "group": "school",
        "language": "ru",
        "question": "Кто сопровождает детей на экскурсии?",
        "relevant": [54],
        "answer": "Тимур и Ксения (сообщение 54).",
        "answerable": True,
        "kind": "change",
    },
    {
        "group": "school",
        "language": "ru",
        "question": "Когда пишем контрольную по математике?",
        "relevant": [63],
        "answer": "В пятницу, 15-го (сообщение 63).",
        "answerable": True,
        "kind": "change",
    },
    {
        "group": "school",
        "language": "ru",
        "question": "Что нужно повторить к контрольной?",
        "relevant": [97],
        "answer": "Таблицу умножения на 7 и 8 и задачи на стр. 52 (сообщение 97).",
        "answerable": True,
        "kind": "fact",
    },
    {
        "group": "school",
        "language": "ru",
        "question": "Когда и где родительское собрание?",
        "relevant": [25, 40],
        "answer": "В четверг в 19:00, в кабинете 241 (сообщения 25 и 40).",
        "answerable": True,
        "kind": "change",
    },
    {
        "group": "school",
        "language": "ru",
        "question": "Нужно ли сдавать деньги на жалюзи?",
        "relevant": [49],
        "answer": "Нет, школа меняет жалюзи сама (сообщение 49).",
        "answerable": True,
        "kind": "decision",
    },
    {
        "group": "school",
        "language": "ru",
        "question": "Во сколько дети вернулись с экскурсии и почему позже?",
        "relevant": [77, 80],
        "answer": "В 14:35: в конце дети пекли булочки (сообщения 77 и 80).",
        "answerable": True,
        "kind": "fact",
    },
    {
        "group": "school",
        "language": "ru",
        "question": "Сколько всего потратили на подарок?",
        "relevant": [88],
        "answer": "11 200 ₽: сертификат за 10 000 и цветы за 1 200 (сообщение 88).",
        "answerable": True,
        "kind": "number",
    },
    {
        "group": "school",
        "language": "ru",
        "question": "Куда дели синий мешок с жирафом?",
        "relevant": [94],
        "answer": "Вера отнесла его на вахту (сообщение 94).",
        "answerable": True,
        "kind": "fact",
    },
    {
        "group": "school",
        "language": "ru",
        "question": "Как одеть ребёнка на праздник 28-го и во сколько начало?",
        "relevant": [102],
        "answer": "Белый верх, чёрный низ; начало в 17:00 в актовом зале (голосовое, сообщение 102).",
        "answerable": True,
        "kind": "fact",
    },
    {
        "group": "school",
        "language": "ru",
        "question": "Кто будет фотографировать на празднике?",
        "relevant": [],
        "answer": None,
        "answerable": False,
        "kind": "open",
    },
    {
        "group": "school",
        "language": "ru",
        "question": "Работает ли продлёнка в день праздника?",
        "relevant": [],
        "answer": None,
        "answerable": False,
        "kind": "open",
    },
    {
        "group": "school",
        "language": "en",
        "question": "How and by when do I pay for the museum trip?",
        "relevant": [14, 48],
        "answer": "By card transfer to Vera, marked «музей», by the 10th (messages 14 and 48).",
        "answerable": True,
        "kind": "fact",
    },
    {
        "group": "school",
        "language": "en",
        "question": "Who is baking for the autumn party tea?",
        "relevant": [102],
        "answer": "Ksenia is baking a pie (voice note, message 102).",
        "answerable": True,
        "kind": "person",
    },
    {
        "group": "school",
        "language": "en",
        "question": "Have all the families paid for the teacher's gift?",
        "relevant": [104],
        "answer": "Yes, 26 of 26 (message 104).",
        "answerable": True,
        "kind": "change",
    },
    {
        "group": "school",
        "language": "en",
        "question": "What is the name of the museum guide?",
        "relevant": [],
        "answer": None,
        "answerable": False,
        "kind": "person",
    },
    {
        "group": "dacha",
        "language": "ru",
        "question": "Во сколько сбор в день переезда?",
        "relevant": [42],
        "answer": "В 7:30; сначала договаривались на 8:00 (сообщение 42).",
        "answerable": True,
        "kind": "change",
    },
    {
        "group": "dacha",
        "language": "ru",
        "question": "Сколько примерно уйдёт на бензин?",
        "relevant": [4],
        "answer": "Около 3 500 ₽, примерно 40 литров туда и обратно (голосовое, сообщение 4).",
        "answerable": True,
        "kind": "number",
    },
    {
        "group": "dacha",
        "language": "ru",
        "question": "За сколько в итоге продали холодильник?",
        "relevant": [53],
        "answer": "За 4 000 ₽ (сообщение 53).",
        "answerable": True,
        "kind": "change",
    },
    {
        "group": "dacha",
        "language": "ru",
        "question": "У кого ключ от калитки на даче?",
        "relevant": [22, 80],
        "answer": "Был у соседки из дома 12; в день переезда она отдала его Наташе (сообщения 22 и 80).",
        "answerable": True,
        "kind": "change",
    },
    {
        "group": "dacha",
        "language": "ru",
        "question": "Почему Дима не мог приехать к началу?",
        "relevant": [55],
        "answer": "Ему нужно было отвезти ребёнка на секцию, поэтому он приезжал только к 9:00 (голосовое, сообщение 55).",
        "answerable": True,
        "kind": "fact",
    },
    {
        "group": "dacha",
        "language": "ru",
        "question": "Через какой въезд заезжать в СНТ на газели и в какие часы можно?",
        "relevant": [50],
        "answer": "Через главный въезд, шлагбаум у второго не работает; грузовым больше 3,5 т — с 8:00 до 20:00 (сообщение 50).",
        "answerable": True,
        "kind": "fact",
    },
    {
        "group": "dacha",
        "language": "ru",
        "question": "Что лежит в коробке №7?",
        "relevant": [37],
        "answer": "Документы и зарядки; её везут в машине, а не в газели (сообщение 37).",
        "answerable": True,
        "kind": "fact",
    },
    {
        "group": "dacha",
        "language": "ru",
        "question": "Сколько Лёша перевёл Паше и за что?",
        "relevant": [90],
        "answer": "4 900 ₽: 3 500 за бензин и 1 400 за ремни (сообщение 90).",
        "answerable": True,
        "kind": "number",
    },
    {
        "group": "dacha",
        "language": "ru",
        "question": "Почему задержались в дороге?",
        "relevant": [77],
        "answer": "Пробка перед мостом из-за ремонта дороги, примерно на 40 минут (голосовое, сообщение 77).",
        "answerable": True,
        "kind": "fact",
    },
    {
        "group": "dacha",
        "language": "ru",
        "question": "Кто заберёт старый ковёр?",
        "relevant": [],
        "answer": None,
        "answerable": False,
        "kind": "open",
    },
    {
        "group": "dacha",
        "language": "ru",
        "question": "Какого числа будут шашлыки на даче?",
        "relevant": [],
        "answer": None,
        "answerable": False,
        "kind": "date",
    },
    {
        "group": "dacha",
        "language": "ru",
        "question": "Какой шланг нужен для стиралки и кто его привезёт?",
        "relevant": [96, 97],
        "answer": "3 метра, 3/4 дюйма; Дима привезёт в среду (сообщения 96 и 97).",
        "answerable": True,
        "kind": "fact",
    },
    {
        "group": "dacha",
        "language": "en",
        "question": "Where did the washing machine go after unloading?",
        "relevant": [81],
        "answer": "Into the shed for now; it will be connected later (message 81).",
        "answerable": True,
        "kind": "fact",
    },
    {
        "group": "dacha",
        "language": "en",
        "question": "What was for lunch at the dacha on moving day?",
        "relevant": [82],
        "answer": "Plov (message 82).",
        "answerable": True,
        "kind": "fact",
    },
    {
        "group": "dacha",
        "language": "en",
        "question": "Until what time could Zhenya stay to help?",
        "relevant": [65],
        "answer": "Until 14:00, after moving her plans (message 65).",
        "answerable": True,
        "kind": "change",
    },
    {
        "group": "dacha",
        "language": "en",
        "question": "How much did renting the van cost?",
        "relevant": [],
        "answer": None,
        "answerable": False,
        "kind": "number",
    },
]

QUESTIONS += [
    {
        "group": "flat",
        "language": "en",
        "question": "What was September's electricity bill, and how much does each person pay?",
        "relevant": [31],
        "answer": "4,680 roubles, 936 each, after Anya corrected her typo of 4,860 (message 31).",
        "answerable": True,
        "kind": "change",
    },
    {
        "group": "flat",
        "language": "en",
        "question": "What did the internet vote decide?",
        "relevant": [35],
        "answer": "Switch to 500 Mbps from the 1st; Marco, Leo, Sasha and Anya for, Katya against (message 35).",
        "answerable": True,
        "kind": "decision",
    },
    {
        "group": "flat",
        "language": "en",
        "question": "When was the internet installation visit?",
        "relevant": [56],
        "answer": "On the 9th, between 10:00 and 14:00 (message 56).",
        "answerable": True,
        "kind": "date",
    },
    {
        "group": "flat",
        "language": "en",
        "question": "What does the faster internet plan cost per month?",
        "relevant": [56],
        "answer": "1,650 a month from the next billing period (message 56).",
        "answerable": True,
        "kind": "number",
    },
    {
        "group": "flat",
        "language": "ru",
        "question": "Какая ошибка была на стиральной машине?",
        "relevant": [39],
        "answer": "E21 (фото, сообщение 39).",
        "answerable": True,
        "kind": "fact",
    },
    {
        "group": "flat",
        "language": "en",
        "question": "How was the washing machine fixed, and who paid?",
        "relevant": [60],
        "answer": "A technician replaced the drain pump; the landlord paid (message 60).",
        "answerable": True,
        "kind": "fact",
    },
    {
        "group": "flat",
        "language": "en",
        "question": "When is the landlord coming to look at the machine and the radiators?",
        "relevant": [64],
        "answer": "Sunday at 12, not Saturday at 11 (message 64).",
        "answerable": True,
        "kind": "change",
    },
    {
        "group": "flat",
        "language": "ru",
        "question": "Когда включат отопление?",
        "relevant": [68],
        "answer": "15-го числа, по графику управляющей компании (голосовое, сообщение 68).",
        "answerable": True,
        "kind": "date",
    },
    {
        "group": "flat",
        "language": "ru",
        "question": "Кто убирает ванную на третьей неделе?",
        "relevant": [73],
        "answer": "Катя: они с Лео поменялись, Лео выносит мусор (сообщение 73).",
        "answerable": True,
        "kind": "change",
    },
    {
        "group": "flat",
        "language": "ru",
        "question": "Какие показания воды передала Катя?",
        "relevant": [86],
        "answer": "Холодная 312, горячая 198 (сообщение 86).",
        "answerable": True,
        "kind": "number",
    },
    {
        "group": "flat",
        "language": "ru",
        "question": "Что хозяйка сказала про депозит Марко?",
        "relevant": [78],
        "answer": "Вернёт в течение недели после осмотра комнаты (сообщение 78).",
        "answerable": True,
        "kind": "decision",
    },
    {
        "group": "flat",
        "language": "en",
        "question": "What is Marco leaving behind, and who takes it?",
        "relevant": [91, 92, 93],
        "answer": "The desk lamp goes to Anya and the shelf to Leo (messages 91 to 93).",
        "answerable": True,
        "kind": "person",
    },
    {
        "group": "flat",
        "language": "en",
        "question": "How much is each person's share of October's power bill?",
        "relevant": [105],
        "answer": "1,048 each, out of 5,240 (message 105).",
        "answerable": True,
        "kind": "number",
    },
    {
        "group": "flat",
        "language": "en",
        "question": "Has this month's internet been paid?",
        "relevant": [],
        "answer": None,
        "answerable": False,
        "kind": "open",
    },
    {
        "group": "flat",
        "language": "en",
        "question": "Where will Marco's farewell dinner be?",
        "relevant": [],
        "answer": None,
        "answerable": False,
        "kind": "open",
    },
    {
        "group": "flat",
        "language": "ru",
        "question": "Как зовут друга Лео, который хочет снять комнату?",
        "relevant": [],
        "answer": None,
        "answerable": False,
        "kind": "person",
    },
    {
        "group": "flat",
        "language": "en",
        "question": "How much deposit will the new flatmate pay?",
        "relevant": [],
        "answer": None,
        "answerable": False,
        "kind": "number",
    },
    {
        "group": "workshop",
        "language": "en",
        "question": "When is the open day now?",
        "relevant": [64],
        "answer": "Saturday the 28th, 12:00 to 17:00 (message 64).",
        "answerable": True,
        "kind": "change",
    },
    {
        "group": "workshop",
        "language": "en",
        "question": "What was wrong with the laser cutter, and is it working again?",
        "relevant": [1, 72],
        "answer": "Its lens cracked; Vadim fitted a new one and the test cut worked (messages 1 and 72).",
        "answerable": True,
        "kind": "change",
    },
    {
        "group": "workshop",
        "language": "ru",
        "question": "Когда Вадим поставит новую линзу?",
        "relevant": [54],
        "answer": "Линза придёт в среду, поставит в четверг вечером (голосовое, сообщение 54).",
        "answerable": True,
        "kind": "date",
    },
    {
        "group": "workshop",
        "language": "en",
        "question": "How much did the filament cost, and who was paid back?",
        "relevant": [12, 70],
        "answer": "Six PETG spools at 1,300, 7,800 in total; the fund paid Greta back (messages 12 and 70).",
        "answerable": True,
        "kind": "number",
    },
    {
        "group": "workshop",
        "language": "ru",
        "question": "Сколько наборов для пайки купили и за сколько?",
        "relevant": [74],
        "answer": "15 наборов за 3 000 ₽ (голосовое, сообщение 74).",
        "answerable": True,
        "kind": "change",
    },
    {
        "group": "workshop",
        "language": "en",
        "question": "What was decided about the membership fee?",
        "relevant": [106],
        "answer": "It rises to 2,800 from the 1st, 5 votes for and 2 against (message 106).",
        "answerable": True,
        "kind": "decision",
    },
    {
        "group": "workshop",
        "language": "en",
        "question": "In the station notes, which board is the sample board?",
        "relevant": [53],
        "answer": "The small blue one (message 53).",
        "answerable": True,
        "kind": "fact",
    },
    {
        "group": "workshop",
        "language": "ru",
        "question": "Для чего большая зелёная плата?",
        "relevant": [51],
        "answer": "Для витрины; на стол для пайки идёт маленькая синяя (сообщение 51).",
        "answerable": True,
        "kind": "fact",
    },
    {
        "group": "workshop",
        "language": "en",
        "question": "Whose is the red multimeter found on the soldering table?",
        "relevant": [80],
        "answer": "Vadim's (message 80).",
        "answerable": True,
        "kind": "person",
    },
    {
        "group": "workshop",
        "language": "en",
        "question": "How many people passed the laser induction on Tuesday?",
        "relevant": [87],
        "answer": "Four, not the three who signed up (voice note, message 87).",
        "answerable": True,
        "kind": "change",
    },
    {
        "group": "workshop",
        "language": "ru",
        "question": "Когда в подвале отключат электричество?",
        "relevant": [60],
        "answer": "В среду с 10:00 до 16:00 (сообщение 60).",
        "answerable": True,
        "kind": "date",
    },
    {
        "group": "workshop",
        "language": "ru",
        "question": "Кто встречает гостей на входе в день открытых дверей?",
        "relevant": [94],
        "answer": "Оля до 14:30, Вадим после (сообщение 94).",
        "answerable": True,
        "kind": "person",
    },
    {
        "group": "workshop",
        "language": "en",
        "question": "Will there be coffee for visitors at the open day?",
        "relevant": [],
        "answer": None,
        "answerable": False,
        "kind": "open",
    },
    {
        "group": "workshop",
        "language": "en",
        "question": "Who is the second helper for setting up the tables at 11:00?",
        "relevant": [],
        "answer": None,
        "answerable": False,
        "kind": "open",
    },
    {
        "group": "workshop",
        "language": "ru",
        "question": "У какого поставщика Вадим заказал линзу?",
        "relevant": [],
        "answer": None,
        "answerable": False,
        "kind": "fact",
    },
    {
        "group": "workshop",
        "language": "en",
        "question": "How much did printing the posters cost?",
        "relevant": [102, 103],
        "answer": "1,200 for 30 posters at 40 each, paid from the fund (messages 102 and 103).",
        "answerable": True,
        "kind": "number",
    },
]

# One day per group to brief live: (group name, index into its days).
BRIEFED = [
    ("garden", 3),
    ("choir", 2),
    ("school", 2),
    ("dacha", 3),
    ("flat", 1),
    ("workshop", 1),
]

# Messages generated from a template, as opposed to individually written ones.
_TEMPLATED = {
    "garden": _BED_CHECKS,
    "choir": _SCORE_COPIES,
    "school": _HOMEWORK_1 + _HOMEWORK_2,
    "dacha": _BOXES,
    "flat": _ROTA,
    "workshop": _STATIONS,
}
_MESSAGE_KEYS = {"id", "speaker", "minute", "text", "voice", "image", "reply_to"}
_TRAPS = {
    None,
    "near_duplicate",
    "same_subject",
    "contradicted",
    "wrong_person",
    "overstated",
    "missing_part",
    "reply_needed",
}
_KINDS = {"fact", "date", "number", "person", "decision", "change", "open"}


def _languages(message):
    """The scripts a message is written in: {"ru"}, {"en"}, both, or none."""
    words = " ".join(message.get(k) or "" for k in ("text", "voice", "image"))
    found = set()
    if any("а" <= ch.lower() <= "я" or ch in "ёЁ" for ch in words):
        found.add("ru")
    if any("a" <= ch.lower() <= "z" for ch in words):
        found.add("en")
    return found


def _cross(language, messages):
    """Whether text in one language rests on messages in another."""
    if language == "mixed":
        return True
    return any(_languages(m) and language not in _languages(m) for m in messages)


def _share(problems, label, count, total, low, high):
    if not low <= count / total <= high:
        problems.append(f"{label}: {count} of {total} is outside {low}-{high}")


def problems():
    """Structural problems in the fixtures; an empty list means none."""
    found = []
    by_name = {}
    if len(GROUPS) != 6:
        found.append(f"{len(GROUPS)} groups, expected 6")
    languages = sorted(group.get("language") for group in GROUPS)
    if languages != ["en", "en", "mixed", "mixed", "ru", "ru"]:
        found.append(f"group languages {languages}")
    voices = images = 0
    for group in GROUPS:
        name = group.get("name")
        missing = {"name", "title", "language", "speakers", "days"} - set(group)
        if missing:
            found.append(f"group {name}: missing {sorted(missing)}")
            continue
        if name in by_name or not name.isascii():
            found.append(f"group {name}: duplicate or non-ascii name")
        speakers = group["speakers"]
        if not 3 <= len(speakers) <= 6 or not all(
            s.startswith("Authored ") for s in speakers
        ):
            found.append(f"group {name}: speakers must be 3-6 Authored names")
        if not 4 <= len(group["days"]) <= 6:
            found.append(f"group {name}: {len(group['days'])} days")
        messages, last_id, last_ago = {}, 0, 26
        for index, day in enumerate(group["days"]):
            ago = day.get("days_ago")
            if not isinstance(ago, int) or not 1 <= ago < last_ago:
                found.append(f"group {name} day {index}: days_ago {ago}")
            last_ago = ago if isinstance(ago, int) else last_ago
            last_minute = -1
            for message in day.get("messages", []):
                ident = message.get("id")
                where = f"group {name} message {ident}"
                if set(message) - _MESSAGE_KEYS - {"forwarded"}:
                    found.append(f"{where}: unknown keys")
                if not isinstance(ident, int) or ident <= last_id:
                    found.append(f"{where}: id not increasing")
                else:
                    last_id = ident
                minute = message.get("minute")
                if not isinstance(minute, int) or minute <= last_minute:
                    found.append(f"{where}: minute not increasing")
                else:
                    last_minute = minute
                if message.get("speaker") not in range(len(speakers)):
                    found.append(f"{where}: unknown speaker")
                if not any(message.get(k) for k in ("text", "voice", "image")):
                    found.append(f"{where}: no content")
                for key in ("text", "voice", "image"):
                    value = message.get(key)
                    if value is not None and (
                        not isinstance(value, str) or not 0 < len(value) < 600
                    ):
                        found.append(f"{where}: {key} must be 1-599 characters")
                if "reply_to" in message and message["reply_to"] not in messages:
                    found.append(f"{where}: reply_to is not an earlier message")
                if "forwarded" in message and not str(message["forwarded"]).startswith(
                    "Authored "
                ):
                    found.append(f"{where}: forwarded label must be Authored")
                voices += "voice" in message
                images += "image" in message
                messages[ident] = message
        if not 70 <= len(messages) <= 140:
            found.append(f"group {name}: {len(messages)} messages")
        templated = _TEMPLATED.get(name, [])
        if any(messages.get(m["id"]) is not m for m in templated):
            found.append(f"group {name}: templated messages not in the group")
        if messages and (len(messages) - len(templated)) / len(messages) < 0.6:
            found.append(f"group {name}: under 60% individually written")
        by_name[name] = (group, messages)
    if not 20 <= voices <= 32 or not 12 <= images <= 22:
        found.append(f"{voices} voice notes and {images} images")

    pairs = supporting = supported = topics = 0
    claim_kinds = {"same_ru": 0, "same_en": 0, "cross": 0}
    claim_keys = {"group", "kind", "language", "title", "text", "citations"}
    claim_keys |= {"supported", "trap"}
    for number, claim in enumerate(CLAIMS):
        where = f"claim {number}"
        if set(claim) != claim_keys:
            found.append(f"{where}: keys {sorted(claim)}")
            continue
        if claim["group"] not in by_name:
            found.append(f"{where}: unknown group")
            continue
        messages = by_name[claim["group"]][1]
        topic = claim["kind"] == "topic"
        topics += topic
        if claim["kind"] not in {"topic", "statement"} or (
            topic != isinstance(claim["title"], str)
        ):
            found.append(f"{where}: kind and title disagree")
        if claim["language"] not in {"en", "ru", "mixed"}:
            found.append(f"{where}: language {claim['language']}")
        if not isinstance(claim["text"], str) or not claim["text"].strip():
            found.append(f"{where}: empty text")
        citations = claim["citations"]
        if not 1 <= len(citations) <= 5:
            found.append(f"{where}: {len(citations)} citations")
        if any(i not in messages for i in citations) or any(
            not isinstance(v, bool) for v in citations.values()
        ):
            found.append(f"{where}: citation ids or labels invalid")
            continue
        pairs += len(citations)
        supporting += sum(citations.values())
        supported += claim["supported"] is True
        if claim["supported"] not in {True, False}:
            found.append(f"{where}: supported must be a bool")
        if claim["supported"] and not any(citations.values()):
            found.append(f"{where}: supported with no supporting citation")
        if claim["trap"] not in _TRAPS or (
            not claim["supported"] and claim["trap"] is None
        ):
            found.append(f"{where}: trap {claim['trap']}")
        cited = [messages[i] for i in citations]
        if _cross(claim["language"], cited):
            claim_kinds["cross"] += 1
        else:
            claim_kinds["same_" + claim["language"]] += 1
    if len(CLAIMS) != 70:
        found.append(f"{len(CLAIMS)} claims, expected 70")
    if not 140 <= pairs <= 170:
        found.append(f"{pairs} claim citations, expected 140-170")
    if CLAIMS and pairs:
        _share(found, "supporting citations", supporting, pairs, 0.5, 0.6)
        _share(found, "fully supported claims", supported, len(CLAIMS), 0.4, 0.6)
        _share(found, "topic claims", topics, len(CLAIMS), 0.5, 0.65)
        _share(found, "Russian claims", claim_kinds["same_ru"], len(CLAIMS), 0.33, 0.47)
        _share(found, "English claims", claim_kinds["same_en"], len(CLAIMS), 0.28, 0.42)
        _share(
            found,
            "cross-language claims",
            claim_kinds["cross"],
            len(CLAIMS),
            0.18,
            0.32,
        )
    traps = {claim.get("trap") for claim in CLAIMS}
    if not _TRAPS <= traps:
        found.append(f"traps never planted: {sorted(map(str, _TRAPS - traps))}")

    question_keys = {"group", "language", "question", "relevant", "answer"}
    question_keys |= {"answerable", "kind"}
    per_group, unanswerable, cross = {}, 0, 0
    for number, question in enumerate(QUESTIONS):
        where = f"question {number}"
        if set(question) != question_keys:
            found.append(f"{where}: keys {sorted(question)}")
            continue
        if question["group"] not in by_name:
            found.append(f"{where}: unknown group")
            continue
        group, messages = by_name[question["group"]]
        per_group[question["group"]] = per_group.get(question["group"], 0) + 1
        if question["language"] not in {"en", "ru"} or question["kind"] not in _KINDS:
            found.append(f"{where}: language or kind")
        relevant = question["relevant"]
        if any(i not in messages for i in relevant):
            found.append(f"{where}: relevant id not in its group")
            continue
        answerable = question["answerable"]
        if answerable is not (bool(relevant) and question["answer"] is not None):
            found.append(f"{where}: answerable disagrees with relevant and answer")
        if question["kind"] == "open" and answerable:
            found.append(f"{where}: an open question must be unanswerable")
        unanswerable += not answerable
        if relevant:
            cross += _cross(question["language"], [messages[i] for i in relevant])
        else:
            cross += group["language"] not in {question["language"], "mixed"}
    if len(QUESTIONS) != 100:
        found.append(f"{len(QUESTIONS)} questions, expected 100")
    for name, count in per_group.items():
        if not 14 <= count <= 20:
            found.append(f"group {name}: {count} questions")
    if set(per_group) != set(by_name):
        found.append("every group needs questions")
    if QUESTIONS:
        _share(
            found, "unanswerable questions", unanswerable, len(QUESTIONS), 0.15, 0.25
        )
        _share(found, "cross-language questions", cross, len(QUESTIONS), 0.15, 0.25)

    if sorted(name for name, _ in BRIEFED) != sorted(by_name):
        found.append("BRIEFED needs exactly one day per group")
    for name, index in BRIEFED:
        days = by_name.get(name, ({"days": []},))[0]["days"]
        if not 0 <= index < len(days):
            found.append(f"BRIEFED {name}: no day {index}")
        elif not 12 <= len(days[index]["messages"]) <= 40:
            found.append(f"BRIEFED {name}: day {index} has the wrong size")
    return found
