const router = require('express').Router();
const OpenAI = require('openai');
const ChatMessage = require('../models/ChatMessage');

router.get('/', async (req, res) => {
  const messages = await ChatMessage.find();
  res.json(messages);
});

router.post('/', async (req, res) => {
  try {
    const { message } = req.body;
    if (!message) {
      return res.status(400).json({ error: 'Message is required' });
    }

    const userMessage = await ChatMessage.create({ message, fromAI: false });
    const completion = await openai.chat.completions.create({
      model: 'gpt-3.5-turbo',
      messages: [{ role: 'user', content: message }]
    });
    const aiText = completion.choices[0].message.content;
    const aiMessage = await ChatMessage.create({ message: aiText, fromAI: true });
    res.json([userMessage, aiMessage]);
  } catch (err) {
    console.error(err);
    res.status(500).json({ error: 'Failed to generate response' });
  }
});

module.exports = router;
