const router = require('express').Router();
const OpenAI = require('openai');
const ChatMessage = require('../models/ChatMessage');

const openai = new OpenAI({
  apiKey: process.env.OPENAI_API_KEY
});

router.get('/', async (req, res) => {
  const messages = await ChatMessage.find();
  res.json(messages);
});

router.post('/', async (req, res) => {
  const { message } = req.body;
  const userMessage = await ChatMessage.create({ message, fromAI: false });
  const completion = await openai.chat.completions.create({
    model: 'gpt-3.5-turbo',
    messages: [{ role: 'user', content: message }]
  });
  const aiText = completion.choices[0].message.content;
  const aiMessage = await ChatMessage.create({ message: aiText, fromAI: true });
  res.json([userMessage, aiMessage]);
});

module.exports = router;
